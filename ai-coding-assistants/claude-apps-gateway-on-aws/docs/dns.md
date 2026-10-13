# DNS Setup Options

Claude Code's `/login` requires the gateway hostname to resolve **only** to private
IPs. This stack publishes the gateway A-record in a **Route 53 Private Hosted
Zone**, which resolves only inside the VPC. The developer machine must therefore
reach that private zone. Pick the option that matches how developers connect.

## Zone scoping

The private hosted zone is named after the **gateway FQDN itself**
(`claude-gateway.corp.example.com`), with the ALB alias record at the zone apex —
it is **not** a private copy of the parent domain (`corp.example.com`).

This matters because a VPC-associated private hosted zone is authoritative for its
entire zone name: queries that match the zone are answered only from that zone and
never fall through to public DNS. If the private zone were created at the domain
apex, every other host in the corporate domain (SSO, internal git, package mirrors,
…) would return NXDOMAIN for VPC workloads and for VPN clients using the VPC
resolver. Scoping the zone to the single FQDN overrides only the gateway name and
leaves the rest of the domain resolving normally.

## Option A: Client VPN uses VPC DNS

Use this when AWS Client VPN connects directly to the gateway VPC.

```text
Developer laptop
  -> Client VPN DNS setting
  -> VPC resolver
  -> Route 53 Private Hosted Zone
  -> internal ALB alias
```

Set the Client VPN endpoint DNS server to the VPC resolver address, for example
`10.0.0.2` when the VPC CIDR is `10.0.0.0/16`. This is the default assumed by the
[Client VPN walkthrough](client-vpn.md).

## Option B: Corporate DNS forwards to Route 53 Resolver

Use this when developers already use corporate DNS over VPN, Direct Connect, or
ZTNA.

```text
Developer laptop
  -> corporate DNS
  -> conditional forwarder for claude-gateway.corp.example.com
  -> Route 53 Resolver inbound endpoint
  -> Route 53 Private Hosted Zone
```

Create a Route 53 Resolver inbound endpoint in the VPC, then configure corporate
DNS to forward `claude-gateway.corp.example.com` (the gateway FQDN — see
[Zone scoping](#zone-scoping)) to the inbound endpoint IP addresses. The inbound
endpoint IPs are private, so the corporate network must already be connected to the
VPC through VPN or Direct Connect.

AWS reference:
[Forwarding inbound DNS queries to your VPCs](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-forwarding-inbound-queries.html)

## Option C: Devbox inside the VPC

Use this when you do not want to configure laptop VPN/DNS immediately.

```text
Developer SSH session / remote devbox
  -> VPC DNS
  -> Route 53 Private Hosted Zone
  -> internal ALB
```

Run Claude Code from an EC2/WorkSpaces/devbox environment in the VPC. The machine
should use the VPC resolver and have security-group/network access to the internal
ALB.

## Note on corporate proxies

If developer machines route HTTPS through a corporate proxy, the proxy host must
also resolve to private addresses; otherwise add the gateway host to `NO_PROXY` so
the CLI connects directly.
