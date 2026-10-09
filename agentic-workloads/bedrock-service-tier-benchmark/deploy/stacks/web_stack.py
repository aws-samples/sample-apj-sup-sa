"""Web tier: Amplify Hosting for the React SPA (manual deployments, no source repo connection).

The SPA is built locally and uploaded as a zip (see deploy/README.md), so no Git
credentials are stored in the account. Strict security headers are set for every path.
"""

from __future__ import annotations

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_amplify as amplify
from constructs import Construct

_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self' https://*.execute-api.{region}.amazonaws.com https://*.amazoncognito.com "
    "https://cognito-idp.{region}.amazonaws.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class WebStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, domain_name: str = "", **kw) -> None:
        super().__init__(scope, cid, **kw)
        headers = f"""customHeaders:
  - pattern: '**'
    headers:
      - key: Strict-Transport-Security
        value: max-age=63072000; includeSubDomains; preload
      - key: Content-Security-Policy
        value: "{_CSP.format(region=self.region)}"
      - key: X-Content-Type-Options
        value: nosniff
      - key: X-Frame-Options
        value: DENY
      - key: Referrer-Policy
        value: no-referrer
      - key: Permissions-Policy
        value: camera=(), microphone=(), geolocation=()
"""
        self.app = amplify.CfnApp(
            self,
            "App",
            name="bedrock-tier-bench",
            platform="WEB",
            custom_headers=headers,
            custom_rules=[
                # SPA routing: unknown paths serve index.html (assets keep their own paths).
                amplify.CfnApp.CustomRuleProperty(
                    source="</^[^.]+$|\\.(?!(css|js|map|json|svg|png|ico|txt|woff2?)$)([^.]+$)/>",
                    target="/index.html",
                    status="200",
                )
            ],
        )
        self.branch = amplify.CfnBranch(
            self, "Main", app_id=self.app.attr_app_id, branch_name="main", stage="PRODUCTION"
        )
        if domain_name:
            amplify.CfnDomain(
                self,
                "Domain",
                app_id=self.app.attr_app_id,
                domain_name=domain_name.split(".", 1)[1],
                sub_domain_settings=[
                    amplify.CfnDomain.SubDomainSettingProperty(branch_name="main", prefix=domain_name.split(".", 1)[0])
                ],
            ).add_dependency(self.branch)
            self.origin = f"https://{domain_name}"
            # Keep the default-domain export alive: a stack first deployed without a custom
            # domain has the Api stack importing it, and CloudFormation refuses to drop an
            # export that is still in use. (Two-step migration: deploy, then this can go.)
            self.export_value(self.app.attr_default_domain)
        else:
            self.origin = f"https://main.{self.app.attr_default_domain}"
        CfnOutput(self, "AppId", value=self.app.attr_app_id)
        CfnOutput(self, "WebUrl", value=self.origin)
