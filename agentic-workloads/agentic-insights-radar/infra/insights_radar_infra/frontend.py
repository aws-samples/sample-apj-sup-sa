# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Web app hosting: private S3 bucket behind CloudFront, ``/api/*`` proxied to the HTTP API.

One origin for the app and its API, so the browser never needs CORS for ``/api``. Deep links are
rewritten to ``index.html`` by a CloudFront Function on the default behaviour only: custom error
responses would also rewrite genuine API 404s.
"""

from __future__ import annotations

import json
from pathlib import Path

from aws_cdk import Aws, Duration, RemovalPolicy, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cf
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as r53_targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as deploy
from constructs import Construct

ROOT = Path(__file__).resolve().parents[2]
PRICE_CLASSES = {
    "100": cf.PriceClass.PRICE_CLASS_100,
    "200": cf.PriceClass.PRICE_CLASS_200,
    "All": cf.PriceClass.PRICE_CLASS_ALL,
}
SITE = ROOT / "build" / "site"
# The single-page-app rule: a path without a file extension, or ending in "/", is the app's index.
SPA_RULE = """  var uri = request.uri;
  if (uri.indexOf('.') === -1 || uri.endsWith('/')) { request.uri = '/index.html'; }
  return request;"""
# With redirectDomains: a request for one of those names gets a 301 to the same path and query on the primary
# name, so a link to the app's old name keeps working after a rename. Only the listed names: the
# distribution's own *.cloudfront.net address keeps serving the app. OLD_HOSTS and PRIMARY are filled at synth
# from validated DNS names.
REDIRECT_CHECK = """  var host = request.headers.host ? request.headers.host.value.toLowerCase().replace(/\\.$/, '') : '';
  if (OLD_HOSTS.indexOf(host) !== -1) {
    var qs = request.querystring, parts = [];
    for (var key in qs) {
      var values = qs[key].multiValue ? qs[key].multiValue : [qs[key]];
      for (var i = 0; i < values.length; i++) {
        parts.push(key + (values[i].value === '' ? '' : '=' + values[i].value));
      }
    }
    var location = 'https://PRIMARY' + request.uri + (parts.length ? '?' + parts.join('&') : '');
    return { statusCode: 301, statusDescription: 'Moved Permanently', headers: { location: { value: location } } };
  }"""


def redirect_code(primary: str | None = None, old: tuple[str, ...] = ()) -> str:
    """The viewer-request function: the SPA rule, after the redirect check when there are old names."""
    check = ""
    if primary and old:
        check = (
            REDIRECT_CHECK.replace("OLD_HOSTS", json.dumps(list(old))).replace(
                "PRIMARY", primary
            )
            + "\n"
        )
    return (
        "function handler(event) {\n  var request = event.request;\n"
        + check
        + SPA_RULE
        + "\n}"
    )


REWRITE = redirect_code()


def _suffix(name: str) -> str:
    """A construct-id suffix for a domain name: radar-two.example.com -> RadarTwo."""
    return "".join(part.capitalize() for part in name.split(".", 1)[0].split("-"))


def csp(region: str, auth_domain: str, assets_bucket: str) -> str:
    connect = " ".join(
        [
            "'self'",
            f"https://cognito-idp.{region}.amazonaws.com",
            f"https://{auth_domain}",
            f"https://bedrock-agentcore.{region}.amazonaws.com",
            f"https://{assets_bucket}.s3.{region}.amazonaws.com",
            f"https://{assets_bucket}.s3.amazonaws.com",
        ]
    )
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            # Cloudscape sets inline style attributes; no inline scripts are allowed.
            "style-src 'self' 'unsafe-inline'",
            # No remote images: an image URL written by an agent could carry data out as a beacon.
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            f"connect-src {connect}",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ]
    )


class Frontend(Construct):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        prefix: str,
        access_logs: s3.IBucket,
        retain: bool,
        log_key: kms.IKey,
        log_retention: logs.RetentionDays,
        price_class: str = "100",
        domain_name: str | None = None,
        parent_zone_id: str | None = None,
        certificate_arn: str | None = None,
        redirect_domains: tuple[str, ...] = (),
    ) -> None:
        super().__init__(scope, cid)
        self._domain = domain_name
        self._redirects = redirect_domains
        self._zones: dict[str, route53.IHostedZone] = {}
        self._certificate: acm.ICertificate | None = None
        if domain_name and parent_zone_id:
            if Stack.of(self).region != "us-east-1":
                raise ValueError(
                    "parentZoneId needs the stack in us-east-1 (CloudFront certificates live there); use certificateArn"
                )
            parent_name = domain_name.split(".", 1)[1]
            parent = route53.HostedZone.from_hosted_zone_attributes(
                self, "ParentZone", hosted_zone_id=parent_zone_id, zone_name=parent_name
            )
            # The resources that carry the plain ids belong to the name the stack was created with: the domain,
            # or after a rename the first redirect (it was the domain), so a rename adds records and replaces
            # none. A delegation cannot be replaced in place: the new NS record would collide with the old one.
            original = redirect_domains[0] if redirect_domains else domain_name
            suffixes = [
                _suffix(n) for n in (domain_name, *redirect_domains) if n != original
            ]
            if len(set(suffixes)) != len(suffixes) or any(
                x.startswith("V6") for x in suffixes
            ):
                # Construct ids come from the first label: radar-two and radar--two would collide, and so
                # would a name whose id starts V6 (its A record's id is another name's AAAA id); synth would
                # fail with a CDK error instead of this one.
                raise ValueError(
                    "redirectDomains and domainName need first labels that differ in more than hyphens "
                    "and do not start with v6"
                )
            delegations = []
            for name in (domain_name, *redirect_domains):
                suffix = "" if name == original else _suffix(name)
                # Each name's own zone: everything for it lives there and goes with the stack; the parent
                # zone gets one delegation record per name and nothing else.
                zone = route53.PublicHostedZone(self, f"Zone{suffix}", zone_name=name)
                delegations.append(
                    route53.NsRecord(
                        self,
                        f"Delegation{suffix}",
                        zone=parent,
                        record_name=name.split(".", 1)[0],
                        values=zone.hosted_zone_name_servers or [],
                        ttl=Duration.minutes(5),
                    )
                )
                self._zones[name] = zone
            cert = acm.Certificate(
                self,
                "Certificate",
                domain_name=domain_name,
                subject_alternative_names=list(redirect_domains) or None,
                validation=acm.CertificateValidation.from_dns_multi_zone(
                    dict(self._zones)
                )
                if redirect_domains
                else acm.CertificateValidation.from_dns(self._zones[domain_name]),
            )
            for (
                delegation
            ) in delegations:  # validation resolves only once each zone is delegated
                cert.node.add_dependency(delegation)
            self._certificate = cert
        elif domain_name and certificate_arn:
            self._certificate = acm.Certificate.from_certificate_arn(
                self, "Certificate", certificate_arn
            )
        # The hosted zones the stack made (domainName with parentZoneId), by name.
        self.zones: dict[str, route53.IHostedZone] = self._zones
        self._prefix = prefix
        self._price_class = PRICE_CLASSES[price_class]
        self._retain = retain
        self._log_key = log_key
        self._log_retention = log_retention
        self._access_logs = access_logs
        self.bucket = s3.Bucket(
            self,
            "Site",
            encryption=s3.BucketEncryption.S3_MANAGED,  # public static assets; OAC reads them
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            server_access_logs_bucket=access_logs,
            server_access_logs_prefix="site/",
            # Versions let a bad web build be rolled back by restoring the previous objects.
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(noncurrent_version_expiration=Duration.days(30))
            ],
            removal_policy=RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE
            if retain
            else RemovalPolicy.DESTROY,
            auto_delete_objects=not retain,
        )

    def build(
        self, api_domain: str, auth_domain: str, assets_bucket: str, web_acl_arn: str
    ) -> None:
        headers = self._headers(
            "Headers", csp(Aws.REGION, auth_domain, assets_bucket), spa=True
        )
        # The API sets its own, stricter CSP (default-src 'none'), Referrer-Policy (no-referrer, so an
        # unsubscribe token in the URL is never sent on) and HSTS; on /api/* they reach the browser, and these
        # apply only to an answer that has none (scan cycles 42 and 43).
        api_headers = self._headers(
            "ApiHeaders", csp(Aws.REGION, auth_domain, assets_bucket), spa=False
        )
        self._build(api_domain, web_acl_arn, headers, api_headers)

    def _headers(self, cid: str, policy: str, *, spa: bool) -> cf.ResponseHeadersPolicy:
        """The security headers; ``spa`` sets CSP, HSTS and Referrer-Policy over the origin's, the API's do not."""
        return cf.ResponseHeadersPolicy(
            self,
            cid,
            security_headers_behavior=cf.ResponseSecurityHeadersBehavior(
                content_security_policy=cf.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=policy,
                    override=spa,
                ),
                strict_transport_security=cf.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365),
                    include_subdomains=True,
                    override=spa,
                ),
                content_type_options=cf.ResponseHeadersContentTypeOptions(
                    override=True
                ),
                frame_options=cf.ResponseHeadersFrameOptions(
                    frame_option=cf.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cf.ResponseHeadersReferrerPolicy(
                    referrer_policy=cf.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=spa,
                ),
            ),
        )

    def _build(
        self,
        api_domain: str,
        web_acl_arn: str,
        headers: cf.ResponseHeadersPolicy,
        api_headers: cf.ResponseHeadersPolicy,
    ) -> None:
        rewrite = cf.Function(
            self,
            "SpaRewrite",
            code=cf.FunctionCode.from_inline(
                redirect_code(self._domain, self._redirects)
            ),
            runtime=cf.FunctionRuntime.JS_2_0,
        )
        self.distribution = cf.Distribution(
            self,
            "Cdn",
            comment=f"{self._prefix} web app",
            default_root_object="index.html",
            http_version=cf.HttpVersion.HTTP2_AND_3,
            price_class=self._price_class,
            enable_logging=True,
            log_bucket=self._access_logs,
            log_file_prefix="cdn/",
            web_acl_id=web_acl_arn,
            domain_names=[self._domain, *self._redirects] if self._domain else None,
            certificate=self._certificate,
            minimum_protocol_version=cf.SecurityPolicyProtocol.TLS_V1_2_2021,
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=headers,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
                function_associations=[
                    cf.FunctionAssociation(
                        function=rewrite, event_type=cf.FunctionEventType.VIEWER_REQUEST
                    )
                ],
            ),
            additional_behaviors={
                "/api/*": cf.BehaviorOptions(
                    origin=origins.HttpOrigin(
                        api_domain, protocol_policy=cf.OriginProtocolPolicy.HTTPS_ONLY
                    ),
                    viewer_protocol_policy=cf.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cf.AllowedMethods.ALLOW_ALL,
                    cache_policy=cf.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    response_headers_policy=api_headers,
                )
            },
        )
        self.url = (
            f"https://{self._domain or self.distribution.distribution_domain_name}"
        )
        if self._zones:
            target = route53.RecordTarget.from_alias(
                r53_targets.CloudFrontTarget(self.distribution)
            )
            original = self._redirects[0] if self._redirects else self._domain
            for name, zone in self._zones.items():
                suffix = "" if name == original else _suffix(name)
                route53.ARecord(self, f"Alias{suffix}", zone=zone, target=target)
                route53.AaaaRecord(self, f"AliasV6{suffix}", zone=zone, target=target)
        if SITE.exists():
            # Its own log group, so it follows retainData like every other log group (CDK's
            # default one is always retained and outlives the stack).
            log_group = logs.LogGroup(
                self,
                "DeployLogs",
                retention=self._log_retention,
                encryption_key=self._log_key,
                removal_policy=RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE
                if self._retain
                else RemovalPolicy.DESTROY,
            )
            # Old hashed files are kept (no prune), so a browser still holding the previous index.html can
            # load its scripts after an upgrade. They never change, so browsers may cache them for a year.
            assets = deploy.BucketDeployment(
                self,
                "Deploy",
                sources=[deploy.Source.asset(str(SITE), exclude=["index.html"])],
                destination_bucket=self.bucket,
                prune=False,
                cache_control=[
                    deploy.CacheControl.from_string(
                        "public, max-age=31536000, immutable"
                    )
                ],
                memory_limit=512,
                log_group=log_group,
            )
            # index.html last, never cached by browsers, and invalidated at the edge.
            index = deploy.BucketDeployment(
                self,
                "DeployIndex",
                sources=[deploy.Source.asset(str(SITE), exclude=["*", "!index.html"])],
                destination_bucket=self.bucket,
                distribution=self.distribution,
                distribution_paths=["/index.html", "/"],
                prune=False,
                cache_control=[deploy.CacheControl.no_cache()],
                memory_limit=512,
                log_group=log_group,
            )
            index.node.add_dependency(assets)
