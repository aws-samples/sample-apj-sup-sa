# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Deployment settings, read from CDK context (``-c key=value`` or ``cdk.json``)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

APP_NAME = "Agentic Insights Radar"
SOLUTION_VERSION = "1.1.0"
DESCRIPTION = f"Agentic Insights Radar on AWS (sample) v{SOLUTION_VERSION}"
# Printable ASCII without spaces, as deploy.sh requires of every value it passes to synth (scan cycle 70).
_EMAIL = re.compile(r"^[!-?A-~]+@[!-?A-~]+\.[!-?A-~]+$")
_SEED = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_DOMAIN = re.compile(
    r"^(?=.{4,200}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){2,}[a-z]{2,63}$"
)
_ZONE = re.compile(r"^Z[A-Z0-9]{8,31}$")
MAX_REDIRECT_DOMAINS = 3
_OIDC_ISSUER = re.compile(
    r"^https://[a-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/-]*)?$"
)
# An MCP id token issuer: https, no port, no trailing slash (the IAM trust key is "<host and path>:aud").
_MCP_ISSUER = re.compile(r"^https://[a-z0-9.-]+(?:/[A-Za-z0-9._~-]+)*$")
_OIDC_PROVIDER_ARN = re.compile(
    r"^arn:aws[a-z-]*:iam::[0-9]{12}:oidc-provider/(?P<url>[a-z0-9.-]+(?:/[A-Za-z0-9._~-]+)*)$"
)
_OIDC_CLIENT = re.compile(r"^[\x21-\x7e]{1,255}$")
_SECRET_ARN = re.compile(
    r"^arn:aws[a-z-]*:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]{1,512}$"
)
_IDP_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{1,31}$")
_SECRET_VERSION = re.compile(r"^[A-Za-z0-9-]{32,64}$")
# An identity provider's own sign-out service, never an AWS compute service: a principal such as
# lambda.amazonaws.com would let any function or task in the account that is given the role sign people out.
_SERVICE_PRINCIPAL = re.compile(
    r"^(?=.{3,128}$)(?!.*\.amazonaws\.com(?:\.cn)?$)[a-z0-9-]+(?:\.[a-z0-9-]+)+$"
)
AUTH_MODES = ("cognito", "federated")
MAX_ADMIN_EMAILS = 20
_CERT = re.compile(
    r"^arn:aws[a-z-]*:acm:us-east-1:[0-9]{12}:certificate/[0-9a-f-]{36}$"
)
_PREFIX = re.compile(r"^[a-z][a-z0-9-]{1,20}$")
# The prefix names the Cognito hosted-UI domain, which may not contain these words.
_RESERVED_WORDS = ("aws", "amazon", "cognito")
# The retention periods CloudWatch Logs accepts, by the name CDK gives each.
RETENTION_DAYS = {
    1: "ONE_DAY",
    3: "THREE_DAYS",
    5: "FIVE_DAYS",
    7: "ONE_WEEK",
    14: "TWO_WEEKS",
    30: "ONE_MONTH",
    60: "TWO_MONTHS",
    90: "THREE_MONTHS",
    120: "FOUR_MONTHS",
    150: "FIVE_MONTHS",
    180: "SIX_MONTHS",
    365: "ONE_YEAR",
    400: "THIRTEEN_MONTHS",
    545: "EIGHTEEN_MONTHS",
    731: "TWO_YEARS",
    1096: "THREE_YEARS",
    1827: "FIVE_YEARS",
    2192: "SIX_YEARS",
    2557: "SEVEN_YEARS",
    2922: "EIGHT_YEARS",
    3288: "NINE_YEARS",
    3653: "TEN_YEARS",
}
# The model catalog's keys (backend/src/insights_radar/modules/catalog.py; a test keeps them in step).
MODEL_KEYS = (
    "haiku-4-5",
    "nova-2-lite",
    "sonnet-5-5",
    "sonnet-4-6",
    "opus-5-5",
    "fable-5-1",
)
PRICE_CLASSES = ("100", "200", "All")
# How AgentCore Memory (chat history) is encrypted. Its key is create-only, so a deployment keeps the
# setting it was created with: ``data-key`` (the deployment's KMS data key, every install from this
# release) or ``aws-owned`` (an AWS-owned key, every deployment created before it). deploy.sh records
# it and refuses to change it without --allow-data-loss, because the change replaces the memory and
# deletes every user's chat history (CR2-01).
CHAT_MEMORY_KEYS = ("data-key", "aws-owned")
MAX_MONTHLY_BUDGET_USD = 1_000_000
# AWS WAF's own range for a rate-based rule's limit, requests per 5 minutes per IP.
# The API's per-minute ceiling for each model-backed bucket across all users (SR2-11).
MIN_WORKSPACE_RATE = 60
MAX_WORKSPACE_RATE = 100_000
MIN_WAF_RATE_LIMIT = 100
MAX_GUARDRAIL_GENERATION = 99
MAX_WAF_RATE_LIMIT = 2_000_000_000


SLACK_MODES = ("off", "bot", "webhook")
DM_RESOLVERS = ("none", "email", "directory")
EMAIL_MODES = ("off", "ses")
DIGEST_KEYS = (
    "slackMode",
    "dmResolver",
    "directoryRoleArn",
    "directoryEndpoint",
    "emailMode",
    "emailSender",
    "sesIdentityArn",
    "sesDomain",
    "sesHostedZoneId",
    "sesDmarc",
)
MAX_SECRET_GENERATION = 999
_ROLE_ARN = re.compile(
    r"^arn:aws[a-z-]*:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]{1,512}$"
)
_HTTPS_URL = re.compile(
    r"^https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/%-]*)?$"
)
# An SES sending domain may be an apex domain (example.com), unlike the app's own domainName.
_MAIL_DOMAIN = re.compile(
    r"^(?=.{4,200}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_SES_IDENTITY_ARN = re.compile(
    r"^arn:aws[a-z-]*:ses:[a-z0-9-]+:[0-9]{12}:identity/([A-Za-z0-9.@_+-]{3,320})$"
)


def _on_domain(address_domain: str, identity_domain: str) -> bool:
    """SES sends from a verified domain and from its subdomains."""
    return address_domain == identity_domain or address_domain.endswith(
        f".{identity_domain}"
    )


@dataclass(frozen=True, slots=True)
class DigestDelivery:
    """Digest delivery to Slack and email (context ``digestDelivery``, a JSON object). All off by
    default, and off adds nothing to the stack."""

    slack_mode: str = "off"
    dm_resolver: str = "none"
    # The directory lookup (alias -> Slack ID, an HTTPS endpoint you run): the role to assume, its URL.
    directory_role_arn: str | None = None
    directory_endpoint: str | None = None
    email_mode: str = "off"
    email_sender: str | None = None
    # Exactly one with emailMode=ses: an SES identity that already exists, or a domain the stack
    # makes an identity for (Easy DKIM; with sesHostedZoneId also its DKIM CNAMEs in that zone).
    ses_identity_arn: str | None = None
    ses_domain: str | None = None
    ses_hosted_zone_id: str | None = None
    # Also a DMARC p=reject record for sesDomain, only when asked: a domain other mail is sent from
    # (an apex domain, a shared zone) may already have one, or have senders it would reject (scan
    # cycle 3).
    ses_dmarc: bool = False

    @property
    def enabled(self) -> bool:
        return self.slack_mode != "off" or self.email_mode != "off"

    @property
    def email(self) -> bool:
        return self.email_mode == "ses"

    def options(self) -> dict[str, str | bool]:
        """The settings that are not off, as the DeployOptions output records them."""
        out = {
            "slackMode": self.slack_mode,
            "dmResolver": self.dm_resolver,
            "directoryRoleArn": self.directory_role_arn,
            "directoryEndpoint": self.directory_endpoint,
            "emailMode": self.email_mode,
            "emailSender": self.email_sender,
            "sesIdentityArn": self.ses_identity_arn,
            "sesDomain": self.ses_domain,
            "sesHostedZoneId": self.ses_hosted_zone_id,
        }
        kept: dict[str, str | bool] = {
            k: v for k, v in out.items() if v and v not in ("off", "none")
        }
        if self.ses_dmarc:
            kept["sesDmarc"] = (
                True  # as given, so the recorded options read back the same
            )
        return kept

    @classmethod
    def parse(cls, raw: Any) -> DigestDelivery:
        """A JSON object, from cdk.json or ``-c digestDelivery='{...}'``."""
        if raw is None or raw == "":
            return cls()
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raise ValueError("digestDelivery must be a JSON object") from None
        obj: dict[str, Any] | None = raw if isinstance(raw, dict) else None
        if obj is None:  # a ValueError, as for every other option
            raise ValueError("digestDelivery must be a JSON object")
        raw = obj
        unknown = sorted(set(raw) - set(DIGEST_KEYS))
        moved = [
            k
            for k in unknown
            if k in ("slackSecretGeneration", "unsubscribeKeyGeneration")
        ]
        if moved:
            # They were digestDelivery keys in scan cycles 61 and 62; a value from then says where they went.
            raise ValueError(
                f"digestDelivery does not take {', '.join(moved)}: it is a top-level option now "
                "(deploy.sh: AIR_SLACK_SECRET_GENERATION, AIR_UNSUBSCRIBE_KEY_GENERATION; CDK: context "
                "slackSecretGeneration, unsubscribeKeyGeneration), so move the same number there"
                + (
                    f"; it does not take {', '.join(k for k in unknown if k not in moved)} either"
                    f" (keys: {', '.join(DIGEST_KEYS)})"
                    if len(unknown) > len(moved)
                    else ""
                )
            )
        if unknown:
            raise ValueError(
                f"digestDelivery does not take {', '.join(unknown)} (keys: {', '.join(DIGEST_KEYS)})"
            )

        def text(key: str) -> str | None:
            value = raw.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"digestDelivery.{key} must be a string")
            return (value or "").strip() or None

        def choice(key: str, allowed: tuple[str, ...]) -> str:
            value = text(key) or allowed[0]
            if value not in allowed:
                raise ValueError(
                    f"digestDelivery.{key} must be one of {', '.join(allowed)}"
                )
            return value

        slack = choice("slackMode", SLACK_MODES)
        resolver = choice("dmResolver", DM_RESOLVERS)
        email = choice("emailMode", EMAIL_MODES)
        role, endpoint = text("directoryRoleArn"), text("directoryEndpoint")
        sender, identity = text("emailSender"), text("sesIdentityArn")
        domain = (text("sesDomain") or "").lower() or None
        zone = text("sesHostedZoneId")
        dmarc = raw.get("sesDmarc", False)
        if (
            dmarc is not True and dmarc is not False
        ):  # JSON true or false only, not 1 or "yes"
            raise ValueError("digestDelivery.sesDmarc must be true or false")
        if dmarc and domain is None:
            raise ValueError("digestDelivery.sesDmarc needs sesDomain")
        # A DM needs the bot token: a webhook posts to its one fixed channel only.
        if resolver != "none" and slack != "bot":
            raise ValueError(
                "digestDelivery.dmResolver needs slackMode=bot (a webhook cannot send a DM)"
            )
        if (role or endpoint) and resolver != "directory":
            raise ValueError(
                "digestDelivery.directoryRoleArn and directoryEndpoint need dmResolver=directory"
            )
        if role is not None and not _ROLE_ARN.fullmatch(role):
            raise ValueError("digestDelivery.directoryRoleArn must be an IAM role ARN")
        if endpoint is not None and not _HTTPS_URL.fullmatch(endpoint):
            raise ValueError("digestDelivery.directoryEndpoint must be an https URL")
        if bool(role) != bool(endpoint):
            # The sender needs both to look a Slack ID up; with neither, DMs wait for the directory endpoint
            # (the stack outputs the roles to name in it). One alone was never going to work (scan cycle 3).
            raise ValueError(
                "digestDelivery.directoryRoleArn and directoryEndpoint go together: set both, or neither until"
                " the directory endpoint is ready"
            )
        if email == "off":
            if sender or identity or domain or zone:
                raise ValueError(
                    "digestDelivery.emailSender, sesIdentityArn, sesDomain and sesHostedZoneId"
                    " need emailMode=ses"
                )
        else:
            if sender is None:
                raise ValueError(
                    "digestDelivery.emailMode=ses needs emailSender, the From address"
                )
            if not _EMAIL.fullmatch(sender):
                raise ValueError(
                    "digestDelivery.emailSender must be an email address in printable ASCII"
                )
            if bool(identity) == bool(domain):
                raise ValueError(
                    "digestDelivery.emailMode=ses needs exactly one of sesIdentityArn (an existing"
                    " SES identity) or sesDomain (an identity made here)"
                )
            if zone is not None and domain is None:
                raise ValueError("digestDelivery.sesHostedZoneId needs sesDomain")
            if zone is not None and not _ZONE.fullmatch(zone):
                raise ValueError(
                    "digestDelivery.sesHostedZoneId must be a Route 53 hosted zone id"
                )
            if domain is not None and not _MAIL_DOMAIN.fullmatch(domain):
                raise ValueError(
                    "digestDelivery.sesDomain must be a DNS name such as mail.example.com"
                )
            sender_domain = sender.rsplit("@", 1)[1].lower()
            if identity is not None:
                match = _SES_IDENTITY_ARN.fullmatch(identity)
                if match is None:
                    raise ValueError(
                        "digestDelivery.sesIdentityArn must be an SES identity ARN"
                    )
                name = match.group(1).lower()
                on_identity = (
                    sender.lower() == name
                    if "@" in name
                    else _on_domain(sender_domain, name)
                )
            else:
                on_identity = _on_domain(sender_domain, str(domain))
            if not on_identity:
                raise ValueError(
                    "digestDelivery.emailSender must be an address on the SES identity"
                )
        return cls(
            slack_mode=slack,
            dm_resolver=resolver,
            directory_role_arn=role,
            directory_endpoint=endpoint,
            email_mode=email,
            email_sender=sender,
            ses_identity_arn=identity,
            ses_domain=domain,
            ses_hosted_zone_id=zone,
            ses_dmarc=dmarc,
        )


def _flag(get: Any, key: str, default: bool) -> bool:
    """A boolean context value. Only ``true`` and ``false`` (any case) are accepted: ``0``, ``no``
    and ``yes`` used to mean different things for different options, so ``AIR_WEB_SEARCH=0`` left
    web search on while ``AIR_BACKUP=0`` turned backups off (P2-050)."""
    raw = get(key)
    if raw is None or str(raw).strip() == "":
        return default
    value = str(raw).strip().lower()
    if value not in ("true", "false"):
        raise ValueError(f"{key} must be true or false, not {str(raw)!r}")
    return value == "true"


def _raw(get: Any, key: str, default: str) -> str:
    """A context value as text, the default only when it is unset: 0 from cdk.json is a value to check, not
    "unset" (round 44 and 45 code reviews)."""
    value = get(key)
    return default if value is None or value == "" else str(value)


def _digits(text: str) -> bool:
    """ASCII digits with no leading zero: str.isdigit() also accepts fullwidth digits int() would read, and
    deploy.sh compares the recorded values in bash (( )), which reads "030" as octal (round 46 code review)."""
    return (
        text.isascii() and text.isdigit() and (text == "0" or not text.startswith("0"))
    )


def _retention(get: Any, key: str, default: int) -> int:
    raw = _raw(get, key, str(default))
    value = (
        int(raw) if _digits(raw) else -1
    )  # int() also read " 30", "３０" and "30.0" (round 46 code review)
    if value not in RETENTION_DAYS:
        raise ValueError(f"{key} must be one of {sorted(RETENTION_DAYS)}")
    return value


@dataclass(frozen=True, slots=True)
class DeployConfig:
    admin_email: str
    prefix: str = "air"
    alarm_email: str | None = None
    retain_data: bool = True
    web_search: bool = True
    log_retention_days: int = 365
    # The AgentCore runtimes' logs hold chat questions and text from fetched pages: kept for less time.
    agent_log_retention_days: int = 30
    # Chat history in AgentCore Memory (7-365 days). A data-protection request to shorten retention
    # is answered with this option rather than a code change (P2-089).
    chat_memory_days: int = 30
    chat_memory_key: str = "data-key"
    chat_model: str = "sonnet-5-5"
    price_class: str = "100"
    # AWS WAF requests per 5 minutes per IP. An office behind one NAT address shares one limit, and
    # since list pages follow every cursor a few people on the admin and audit pages can use it up,
    # so it is a deploy option rather than a constant (P2-069).
    waf_rate_limit: int = 2000
    # A guardrail deleted outside CloudFormation (another tool's clean-up in a shared account, 2026-11-04)
    # cannot be brought back by an update: CloudFormation still holds its id. A new generation is a new
    # resource with a new name, so the update creates one; the default keeps every stack as it is.
    guardrail_generation: int = 1
    # The same for the digest secrets: raised by one to make that one secret new, for one deleted for
    # good outside CloudFormation. Top-level options like guardrailGeneration, so deploy.sh carries
    # them over and no digestDelivery value can change them (scan cycles 61 and 63).
    slack_secret_generation: int = 0
    unsubscribe_key_generation: int = 0
    # Search, relevance listing, graph traversal and product ask calls per minute across every
    # non-admin user: what a few viewers at their own per-user limit can use up for everyone.
    workspace_rate_per_minute: int = 600
    monthly_budget_usd: int = 0  # 0: no AWS Budget
    reserved_concurrency: bool = False
    backup_plan: bool = False
    # Validation stacks: create the first admin without an email.
    suppress_invitation: bool = False
    # The e2e suite signs test users in with SRP on the MCP client; off, both clients allow only the
    # hosted UI. Turn on only for a stack you run the e2e suite against.
    e2e_sign_in: bool = False
    # Off only during a fresh install (deploy.sh turns it on in a second update): CloudFormation cannot
    # roll back a failed first deployment through protected tables and user pools.
    deletion_protection: bool | None = None
    # A seed bundle under deployment/seeds/<seed>/bundle.json applied on every deploy (profile, module
    # settings, public sources); None: the built-in defaults only.
    seed: str | None = None
    # A custom domain for the web app (and its /api): with parentZoneId the stack makes its own hosted zone
    # for the name, delegates it from the parent zone (one NS record) and issues the certificate; with
    # certificateArn it uses that certificate and leaves DNS to the operator.
    domain_name: str | None = None
    parent_zone_id: str | None = None
    certificate_arn: str | None = None
    # Names the app answered on before a rename (parentZoneId only): each keeps its zone, delegation and DNS
    # records, is added to the certificate and the distribution, and answers with a permanent redirect to
    # domainName, so links and bookmarks to the old name keep working. The first one keeps the stack's
    # original DNS resources (it was domainName before the rename), so renaming replaces nothing.
    redirect_domains: tuple[str, ...] = ()
    # How people sign in. "cognito": the user pool's own accounts, invited by an administrator, with a password
    # and TOTP. "federated": only through one OpenID Connect provider (for example a company single sign-on);
    # anyone the provider lets in becomes a viewer, the emails in admin_emails become administrators, and no
    # account, password or invitation email is created here.
    auth_mode: str = "cognito"
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    # The client secret stays in Secrets Manager: the template carries a dynamic reference, never the value.
    oidc_client_secret_arn: str | None = None
    oidc_provider_name: str = "SSO"
    # Federated: make the listed administrators admins even when the provider does not mark the address
    # verified. Only for a provider whose addresses come from a company directory and cannot be chosen.
    oidc_trust_email: bool = False
    # Federated: the secret version the template resolves. Putting a new client secret value and deploying
    # with its version id is how the user pool picks it up (an unversioned reference is read only when the
    # identity provider resource changes).
    oidc_client_secret_version: str | None = None
    # Federated: the AWS service principal of the provider's user-lifecycle service, for example
    # signout.idp.example.com. Creates a role it may assume to sign this pool's users out
    # (ListUsers, AdminUserGlobalSignOut, DescribeIdentityProvider on this pool only).
    oidc_sign_out_principal: str | None = None
    # Federated mode: the administrators besides admin_email (who always is one).
    admin_emails: tuple[str, ...] = ()
    # Federated mode, optional: an MCP client signs in without a browser. It gets an OpenID Connect id
    # token from mcp_issuer for mcp_audience (its own business how), exchanges it with STS for a session
    # of the stack's MCP role named after the token's subject, and signs /iam/api/* requests with SigV4.
    # The subject must be the user id the sign-in provider gives the same person, so the session maps to
    # the account the web sign-in created. Both empty = off.
    mcp_issuer: str | None = None
    mcp_audience: str | None = None
    # An IAM OIDC provider for mcp_issuer the account already has (IAM allows one per URL per account):
    # used instead of creating one. Its client id list must hold mcp_audience; that is set where it lives.
    mcp_provider_arn: str | None = None
    # Digest delivery to Slack and email (context ``digestDelivery``); all off by default.
    digest: DigestDelivery = field(default_factory=DigestDelivery)

    @property
    def mcp_web_identity(self) -> bool:
        return self.mcp_issuer is not None

    @property
    def federated(self) -> bool:
        return self.auth_mode == "federated"

    @property
    def administrators(self) -> tuple[str, ...]:
        """Every administrator email, lower-case, first administrator first, without repeats."""
        return tuple(
            dict.fromkeys(e.lower() for e in (self.admin_email, *self.admin_emails))
        )

    @property
    def protect(self) -> bool:
        return (
            self.retain_data
            if self.deletion_protection is None
            else self.deletion_protection
        )

    @property
    def alarm_address(self) -> str:
        """Who is told when an alarm fires: the alarm email, else the first administrator."""
        return self.alarm_email or self.admin_email

    def options_json(self) -> str:
        """The options an update must keep, as deploy.sh reads them back from the DeployOptions output.

        ``e2eSignIn`` is recorded but never *kept*: deploy.sh asks for it on every deploy and only
        reads the recorded value to say that it is turning SRP sign-in off again. Without it in the
        output that message could never print, and nothing told an operator that a stack still had
        SRP password sign-in open (P2-017)."""
        return json.dumps(
            {
                "retainData": str(self.retain_data).lower(),
                "webSearch": str(self.web_search).lower(),
                "logRetentionDays": str(self.log_retention_days),
                "agentLogRetentionDays": str(self.agent_log_retention_days),
                "chatMemoryDays": str(self.chat_memory_days),
                "chatMemoryKey": self.chat_memory_key,
                "chatModel": self.chat_model,
                "priceClass": self.price_class,
                "wafRateLimit": str(self.waf_rate_limit),
                "guardrailGeneration": str(self.guardrail_generation),
                "slackSecretGeneration": str(self.slack_secret_generation),
                "unsubscribeKeyGeneration": str(self.unsubscribe_key_generation),
                "workspaceRatePerMinute": str(self.workspace_rate_per_minute),
                "monthlyBudgetUsd": str(self.monthly_budget_usd),
                "reservedConcurrency": str(self.reserved_concurrency).lower(),
                "backupPlan": str(self.backup_plan).lower(),
                "suppressInvitation": str(self.suppress_invitation).lower(),
                "e2eSignIn": str(self.e2e_sign_in).lower(),
                **({"seed": self.seed} if self.seed else {}),
                **({"domainName": self.domain_name} if self.domain_name else {}),
                **(
                    {"redirectDomains": ",".join(self.redirect_domains)}
                    if self.redirect_domains
                    else {}
                ),
                **(
                    {"parentZoneId": self.parent_zone_id} if self.parent_zone_id else {}
                ),
                **(
                    {"certificateArn": self.certificate_arn}
                    if self.certificate_arn
                    else {}
                ),
                **(
                    {
                        "authMode": self.auth_mode,
                        "oidcIssuer": self.oidc_issuer or "",
                        "oidcClientId": self.oidc_client_id or "",
                        "oidcClientSecretArn": self.oidc_client_secret_arn or "",
                        "oidcProviderName": self.oidc_provider_name,
                        "oidcTrustEmail": str(self.oidc_trust_email).lower(),
                        "oidcClientSecretVersion": self.oidc_client_secret_version
                        or "",
                        "oidcSignOutPrincipal": self.oidc_sign_out_principal or "",
                        "adminEmails": ",".join(self.admin_emails),
                    }
                    if self.federated
                    else {}
                ),
                **(
                    {
                        "mcpIssuer": self.mcp_issuer or "",
                        "mcpAudience": self.mcp_audience or "",
                        "mcpProviderArn": self.mcp_provider_arn or "",
                    }
                    if self.mcp_web_identity
                    else {}
                ),
                **(
                    {
                        "digestDelivery": json.dumps(
                            self.digest.options(), separators=(",", ":")
                        )
                    }
                    if self.digest.enabled
                    else {}
                ),
                # Which release the stack runs; read by operators, never kept as an option (P3-051).
                "solutionVersion": SOLUTION_VERSION,
                **({"alarmEmail": self.alarm_email} if self.alarm_email else {}),
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_context(cls, get: Any) -> DeployConfig:
        email = str(get("adminEmail") or "").strip()
        if not _EMAIL.fullmatch(email):
            raise ValueError(
                "set the first administrator's email: cdk deploy -c adminEmail=you@example.com"
            )
        prefix = str(get("prefix") or "air")
        if not _PREFIX.fullmatch(prefix) or prefix.endswith("-") or "--" in prefix:
            raise ValueError(
                "prefix must be 2-21 lower-case letters, digits or single hyphens, starting with a letter"
            )
        if any(word in prefix for word in _RESERVED_WORDS):
            raise ValueError(
                "prefix may not contain aws, amazon or cognito (Cognito rejects them in its domain)"
            )
        alarm = str(get("alarmEmail") or "").strip() or None
        if alarm and not _EMAIL.fullmatch(alarm):
            raise ValueError("alarmEmail is not a valid email address")
        retention = _retention(get, "logRetentionDays", 365)
        agent_retention = _retention(get, "agentLogRetentionDays", 30)
        memory_raw = _raw(get, "chatMemoryDays", "30")
        if not _digits(memory_raw) or not 7 <= int(memory_raw) <= 365:
            raise ValueError(
                "chatMemoryDays must be a whole number of days from 7 to 365"
            )
        memory_key = str(get("chatMemoryKey") or "data-key")
        if memory_key not in CHAT_MEMORY_KEYS:
            raise ValueError(
                f"chatMemoryKey must be one of {', '.join(CHAT_MEMORY_KEYS)}"
            )
        chat_model = str(get("chatModel") or "sonnet-5-5")
        if chat_model not in MODEL_KEYS:
            raise ValueError(f"chatModel must be one of {', '.join(MODEL_KEYS)}")
        price_class = str(get("priceClass") or "100")
        if price_class not in PRICE_CLASSES:
            raise ValueError(f"priceClass must be one of {', '.join(PRICE_CLASSES)}")
        rate_raw = _raw(get, "wafRateLimit", "2000")
        if (
            not _digits(rate_raw)
            or not MIN_WAF_RATE_LIMIT <= int(rate_raw) <= MAX_WAF_RATE_LIMIT
        ):
            raise ValueError(
                f"wafRateLimit must be a whole number of requests per 5 minutes per IP, "
                f"{MIN_WAF_RATE_LIMIT} to {MAX_WAF_RATE_LIMIT}"
            )
        generation_raw = _raw(get, "guardrailGeneration", "1")
        if not _digits(generation_raw) or not (
            1 <= int(generation_raw) <= MAX_GUARDRAIL_GENERATION
        ):
            raise ValueError(
                f"guardrailGeneration must be a whole number from 1 to {MAX_GUARDRAIL_GENERATION}"
            )
        secret_generations = []
        for key in ("slackSecretGeneration", "unsubscribeKeyGeneration"):
            value_raw = _raw(get, key, "0")
            if not _digits(value_raw) or int(value_raw) > MAX_SECRET_GENERATION:
                raise ValueError(
                    f"{key} must be a whole number from 0 to {MAX_SECRET_GENERATION}"
                )
            secret_generations.append(int(value_raw))
        workspace_raw = _raw(get, "workspaceRatePerMinute", "600")
        if (
            not _digits(workspace_raw)
            or not MIN_WORKSPACE_RATE <= int(workspace_raw) <= MAX_WORKSPACE_RATE
        ):
            raise ValueError(
                f"workspaceRatePerMinute must be a whole number of calls per minute, "
                f"{MIN_WORKSPACE_RATE} to {MAX_WORKSPACE_RATE}"
            )
        budget_raw = _raw(get, "monthlyBudgetUsd", "0")
        if not _digits(budget_raw) or int(budget_raw) > MAX_MONTHLY_BUDGET_USD:
            raise ValueError(
                f"monthlyBudgetUsd must be a whole number of US dollars from 0 to {MAX_MONTHLY_BUDGET_USD}"
            )
        seed = str(get("seed") or "").strip() or None
        if seed is not None and not _SEED.fullmatch(seed):
            raise ValueError(
                "seed must name a folder under deployment/seeds (1-40 lower-case letters, digits, hyphens)"
            )
        domain = str(get("domainName") or "").strip().lower() or None
        zone = str(get("parentZoneId") or "").strip() or None
        cert = str(get("certificateArn") or "").strip() or None
        if domain is not None and not _DOMAIN.fullmatch(domain):
            raise ValueError("domainName must be a DNS name such as radar.example.com")
        if (zone or cert) and domain is None:
            raise ValueError("parentZoneId and certificateArn need domainName")
        if domain is not None and bool(zone) == bool(cert):
            raise ValueError(
                "domainName needs exactly one of parentZoneId (DNS and certificate made here) or certificateArn"
            )
        redirects = tuple(
            dict.fromkeys(
                d.strip().lower()
                for d in str(get("redirectDomains") or "").split(",")
                if d.strip()
            )
        )
        if redirects:
            if domain is None or zone is None:
                raise ValueError("redirectDomains needs domainName and parentZoneId")
            if len(redirects) > MAX_REDIRECT_DOMAINS:
                raise ValueError(
                    f"redirectDomains takes at most {MAX_REDIRECT_DOMAINS} names"
                )
            parent = domain.split(".", 1)[1]
            for name in redirects:
                if (
                    not _DOMAIN.fullmatch(name)
                    or name == domain
                    or name.split(".", 1)[1] != parent
                ):
                    raise ValueError(
                        f"redirectDomains must be other names in {parent} (the parent zone), such as old.{parent}"
                    )
        if zone is not None and not _ZONE.fullmatch(zone):
            raise ValueError(
                "parentZoneId must be a Route 53 hosted zone id such as Z0123456789ABCDEFGHIJ"
            )
        if cert is not None and not _CERT.fullmatch(cert):
            raise ValueError(
                "certificateArn must be an ACM certificate ARN in us-east-1 (CloudFront requires it)"
            )
        auth_mode = str(get("authMode") or "cognito").strip()
        if auth_mode not in AUTH_MODES:
            raise ValueError(f"authMode must be one of {', '.join(AUTH_MODES)}")
        issuer = str(get("oidcIssuer") or "").strip() or None
        client_id = str(get("oidcClientId") or "").strip() or None
        secret_arn = str(get("oidcClientSecretArn") or "").strip() or None
        idp_name = str(get("oidcProviderName") or "SSO").strip()
        secret_version = str(get("oidcClientSecretVersion") or "").strip() or None
        sign_out = str(get("oidcSignOutPrincipal") or "").strip() or None
        extra = tuple(
            e.strip() for e in str(get("adminEmails") or "").split(",") if e.strip()
        )
        if auth_mode == "federated":
            if issuer is None or not _OIDC_ISSUER.fullmatch(issuer):
                raise ValueError(
                    "authMode=federated needs oidcIssuer, the provider's https issuer URL"
                )
            if client_id is None or not _OIDC_CLIENT.fullmatch(client_id):
                raise ValueError(
                    "authMode=federated needs oidcClientId, the client id the provider issued"
                )
            if secret_arn is None or not _SECRET_ARN.fullmatch(secret_arn):
                raise ValueError(
                    "authMode=federated needs oidcClientSecretArn, a Secrets Manager secret holding the client secret"
                )
            if not _IDP_NAME.fullmatch(idp_name) or idp_name.lower() in (
                "cognito",
                "google",
                "facebook",
            ):
                raise ValueError(
                    "oidcProviderName must be 2-32 letters, digits or hyphens, starting with a letter"
                )
            if secret_version is not None and not _SECRET_VERSION.fullmatch(
                secret_version
            ):
                raise ValueError(
                    "oidcClientSecretVersion must be a Secrets Manager version id"
                )
            if sign_out is not None and not _SERVICE_PRINCIPAL.fullmatch(sign_out):
                raise ValueError(
                    "oidcSignOutPrincipal must be the identity provider's sign-out service principal, such as signout.idp.example.com, not an amazonaws.com service"
                )
            if len(extra) > MAX_ADMIN_EMAILS or not all(
                _EMAIL.fullmatch(e) for e in extra
            ):
                raise ValueError(
                    f"adminEmails must be up to {MAX_ADMIN_EMAILS} email addresses, comma-separated"
                )
        elif issuer or client_id or secret_arn or extra or secret_version or sign_out:
            raise ValueError(
                "oidcIssuer, oidcClientId, oidcClientSecretArn, oidcClientSecretVersion,"
                " oidcSignOutPrincipal and adminEmails need authMode=federated"
            )
        mcp_issuer = str(get("mcpIssuer") or "").strip() or None
        mcp_audience = str(get("mcpAudience") or "").strip() or None
        mcp_provider = str(get("mcpProviderArn") or "").strip() or None
        if mcp_issuer or mcp_audience or mcp_provider:
            if auth_mode != "federated":
                raise ValueError(
                    "mcpIssuer and mcpAudience need authMode=federated: an MCP session is mapped to the"
                    " account the provider's sign-in created"
                )
            if mcp_issuer is None or not _MCP_ISSUER.fullmatch(mcp_issuer):
                raise ValueError(
                    "mcpIssuer must be the https issuer URL of the MCP clients' id tokens, without a port or a trailing slash"
                )
            # The account an MCP session acts as is named after the token's sub (<oidcProviderName>_<sub>), and
            # a sub is only an id at the issuer that set it: a token from another issuer could name another user.
            if issuer is None or mcp_issuer != issuer.removesuffix("/"):
                raise ValueError(
                    "mcpIssuer must be oidcIssuer (without a trailing slash): the MCP session acts as the account"
                    " named after the token's sub, which identifies a user only at the web app's own provider"
                )
            if mcp_audience is None or not _OIDC_CLIENT.fullmatch(mcp_audience):
                raise ValueError(
                    "mcpAudience must be the id tokens' audience (client id), 1-255 printable characters"
                )
            if mcp_provider is not None:
                m = _OIDC_PROVIDER_ARN.fullmatch(mcp_provider)
                if not m or m["url"] != mcp_issuer.removeprefix("https://"):
                    raise ValueError(
                        "mcpProviderArn must be the IAM OIDC provider ARN of mcpIssuer in this account"
                    )
        protection = get("deletionProtection")
        if protection is not None and str(protection).strip() != "":
            _flag(get, "deletionProtection", True)  # refuse anything but true or false
        return cls(
            admin_email=email,
            prefix=prefix,
            alarm_email=alarm,
            retain_data=_flag(get, "retainData", True),
            web_search=_flag(get, "webSearch", True),
            log_retention_days=retention,
            agent_log_retention_days=agent_retention,
            chat_memory_days=int(memory_raw),
            chat_memory_key=memory_key,
            chat_model=chat_model,
            price_class=price_class,
            waf_rate_limit=int(rate_raw),
            guardrail_generation=int(generation_raw),
            slack_secret_generation=secret_generations[0],
            unsubscribe_key_generation=secret_generations[1],
            workspace_rate_per_minute=int(workspace_raw),
            monthly_budget_usd=int(budget_raw),
            reserved_concurrency=_flag(get, "reservedConcurrency", False),
            backup_plan=_flag(get, "backupPlan", False),
            suppress_invitation=_flag(get, "suppressInvitation", False),
            e2e_sign_in=_flag(get, "e2eSignIn", False),
            deletion_protection=None
            if protection is None
            else str(protection).lower() != "false",
            seed=seed,
            domain_name=domain,
            parent_zone_id=zone,
            certificate_arn=cert,
            redirect_domains=redirects,
            auth_mode=auth_mode,
            oidc_issuer=issuer,
            oidc_client_id=client_id,
            oidc_client_secret_arn=secret_arn,
            oidc_provider_name=idp_name,
            oidc_trust_email=_flag(get, "oidcTrustEmail", False),
            oidc_client_secret_version=secret_version,
            oidc_sign_out_principal=sign_out,
            admin_emails=extra,
            mcp_issuer=mcp_issuer,
            mcp_audience=mcp_audience,
            mcp_provider_arn=mcp_provider,
            digest=DigestDelivery.parse(get("digestDelivery")),
        )
