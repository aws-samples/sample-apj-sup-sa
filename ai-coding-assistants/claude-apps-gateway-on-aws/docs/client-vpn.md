# AWS Client VPN Setup (full walkthrough)

AWS Client VPN is **not** created by this stack. It is the most common way to give
developers on laptops a private path into the gateway VPC, which is required
because Claude Code's `/login` only connects to a gateway whose hostname resolves
to private IPs. See the README ["AWS Client VPN"](../README.md#aws-client-vpn)
section for the short version; this page is the detailed reference.

Two things must line up:

1. A **network path** from the laptop to the internal ALB (TCP 443).
2. A **DNS path** so the gateway hostname resolves to the ALB's private IPs — see
   [`docs/dns.md`](dns.md).

The walkthrough uses **mutual certificate authentication**, the simplest option
for a small team. All values are examples — replace the gateway hostname
(`claude-gateway.corp.example.com`), IDs, and ARNs with your own. Commands target
`us-east-1` to match the stack defaults; add `--profile <your-profile>` if you use
a named AWS CLI profile.

## Step 1. Generate certificates and import the server certificate into ACM

Client VPN requires a TLS server certificate in ACM. With mutual authentication the
client certificate must be issued by the same CA, so generate everything with
[easy-rsa](https://github.com/OpenVPN/easy-rsa):

```bash
git clone https://github.com/OpenVPN/easy-rsa.git && cd easy-rsa/easyrsa3
./easyrsa init-pki
./easyrsa build-ca nopass                                   # your private CA
./easyrsa --san=DNS:server build-server-full server nopass  # VPN server certificate
./easyrsa build-client-full client1 nopass                  # one certificate per developer

aws acm import-certificate \
  --certificate fileb://pki/issued/server.crt \
  --private-key fileb://pki/private/server.key \
  --certificate-chain fileb://pki/ca.crt \
  --region us-east-1
```

Note the certificate ARN in the response. Because the server and client
certificates share one CA, the same ARN also serves as the client root certificate
chain in the next step.

## Step 2. Create the Client VPN endpoint

Three decisions matter here:

- **Client CIDR** (`172.16.0.0/22` below): must not overlap the VPC CIDR
  (`10.0.0.0/16`) and must be between `/22` and `/12`. Overlap would make return
  routing ambiguous.
- **DNS servers must point at the VPC resolver** — `10.0.0.2` for the default
  `10.0.0.0/16` VPC (VPC CIDR base + 2). This is what lets developer laptops
  resolve the Route 53 private hosted zone; without it, `/login` cannot resolve
  the gateway hostname at all.
- **Split tunnel**: only traffic to routed CIDRs (the VPC) goes through the tunnel;
  normal internet traffic stays off the VPN.

```bash
aws ec2 create-client-vpn-endpoint \
  --client-cidr-block 172.16.0.0/22 \
  --server-certificate-arn arn:aws:acm:us-east-1:<ACCOUNT_ID>:certificate/<CERTIFICATE_ID> \
  --authentication-options 'Type=certificate-authentication,MutualAuthentication={ClientRootCertificateChainArn=arn:aws:acm:us-east-1:<ACCOUNT_ID>:certificate/<CERTIFICATE_ID>}' \
  --connection-log-options Enabled=false \
  --dns-servers 10.0.0.2 \
  --split-tunnel \
  --vpc-id vpc-0123456789abcdef0 \
  --transport-protocol udp \
  --region us-east-1
```

## Step 3. Associate a subnet and authorize access

Associate one of the stack's private application subnets, then allow VPN users to
reach the VPC. Associating a subnet automatically adds the route for the VPC CIDR,
so no explicit route entry is needed.

```bash
aws ec2 associate-client-vpn-target-network \
  --client-vpn-endpoint-id cvpn-endpoint-0123456789abcdef0 \
  --subnet-id subnet-0123456789abcdef0 \
  --region us-east-1

aws ec2 authorize-client-vpn-ingress \
  --client-vpn-endpoint-id cvpn-endpoint-0123456789abcdef0 \
  --target-network-cidr 10.0.0.0/16 \
  --authorize-all-groups \
  --region us-east-1
```

The association takes 5–15 minutes. Wait until the endpoint status is `available`
before connecting — connection attempts made earlier just hang:

```bash
aws ec2 describe-client-vpn-endpoints \
  --client-vpn-endpoint-ids cvpn-endpoint-0123456789abcdef0 \
  --query 'ClientVpnEndpoints[0].Status.Code' --output text --region us-east-1
```

Two operational notes:

- **No security-group change is needed.** VPN traffic is source-NATed to the
  associated subnet's IPs (inside `10.0.0.0/16`), which the default
  `allowedClientCidrs` of `10.0.0.0/8` already allows at the ALB.
- **Cost**: each subnet association bills hourly (~$0.10/h ≈ $73/month) plus
  ~$0.05/h per active connection. One subnet is enough for personal use; associate
  a second AZ only when you need the availability.

## Step 4. Build the client profile

Export the endpoint's OpenVPN configuration and append the client certificate and
key inline:

```bash
aws ec2 export-client-vpn-client-configuration \
  --client-vpn-endpoint-id cvpn-endpoint-0123456789abcdef0 \
  --output text --region us-east-1 > client1.ovpn

{ echo "<cert>"; cat pki/issued/client1.crt; echo "</cert>";
  echo "<key>";  cat pki/private/client1.key; echo "</key>"; } >> client1.ovpn
chmod 600 client1.ovpn
```

The `.ovpn` file embeds a private key — hand it to each developer over a secure
channel, and issue one certificate per person so access can be revoked
individually.

## Step 5. Connect and verify

Install the [AWS Client VPN desktop app](https://aws.amazon.com/vpn/client-vpn-download/)
(or any OpenVPN-compatible client), add `client1.ovpn` via **File → Manage
Profiles**, and connect. Then verify the DNS path and the network path separately:

```bash
# DNS path: must return the internal ALB's private IPs (10.0.x.x)
dig +short claude-gateway.corp.example.com

# Network path: gateway health endpoint through the tunnel — expect 200
curl -s -o /dev/null -w "%{http_code}\n" https://claude-gateway.corp.example.com/healthz
```

With both checks passing, run `claude` and `/login` (requires the managed settings
file on the machine — see [`docs/operations.md`](operations.md#developer-managed-settings)).

## Teardown

Client VPN is billed while it exists, so remove it when you tear down the stack:

```bash
aws ec2 disassociate-client-vpn-target-network \
  --client-vpn-endpoint-id cvpn-endpoint-0123456789abcdef0 \
  --association-id cvpn-assoc-0123456789abcdef0 --region us-east-1

aws ec2 delete-client-vpn-endpoint \
  --client-vpn-endpoint-id cvpn-endpoint-0123456789abcdef0 --region us-east-1
```

## Private network patterns (alternatives to Client VPN)

Client VPN is one option. Any of these satisfies the private-network contract, as
long as the machine can both **resolve the hostname to private IPs** and **connect
to those IPs on TCP 443**:

- AWS Client VPN into the gateway VPC.
- Corporate VPN or Direct Connect into the VPC, often through Transit Gateway.
- ZTNA or overlay network that routes to the VPC.
- Developer WorkSpaces, EC2 devboxes, or Cloud9-like environments inside the VPC.

```text
Developer machine
  can resolve claude-gateway.corp.example.com to private IPs
  can connect to those private IPs on TCP 443
```

## AWS references

- [AWS Client VPN overview](https://docs.aws.amazon.com/vpn/latest/clientvpn-admin/what-is.html)
- [AWS Client VPN getting started](https://docs.aws.amazon.com/vpn/latest/clientvpn-admin/cvpn-getting-started.html)
- [Client VPN target networks](https://docs.aws.amazon.com/vpn/latest/clientvpn-admin/cvpn-working-target.html)
- [Client VPN routes](https://docs.aws.amazon.com/vpn/latest/clientvpn-admin/cvpn-working-routes.html)
- [Client VPN authorization rules](https://docs.aws.amazon.com/vpn/latest/clientvpn-admin/cvpn-working-rules.html)
