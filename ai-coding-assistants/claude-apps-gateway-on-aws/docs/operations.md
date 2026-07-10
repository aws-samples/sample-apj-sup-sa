# Operations

Day-2 operations for the deployed gateway: pushing managed settings to developer
machines, toggling forced-gateway login while testing, post-deploy smoke tests, and
troubleshooting.

## Developer managed settings

Deploy [`managed-settings.json`](managed-settings.json) through your managed
settings channel (MDM, or on disk on each machine). Edit `forceLoginGatewayUrl` to
your own gateway URL first:

```json
{
  "forceLoginMethod": "gateway",
  "forceLoginGatewayUrl": "https://claude-gateway.corp.example.com"
}
```

With both keys set, `/login` opens directly on the **Cloud gateway** screen with the
URL filled in. `forceLoginGatewayUrl` is honored only at the managed-policy tier and
is ignored in a developer's own settings files — it must come from the file you push
to machines.

Managed settings live in a per-OS system location (highest precedence, cannot be
overridden by user/project settings):

| OS | Path |
|---|---|
| macOS | `/Library/Application Support/ClaudeCode/managed-settings.json` |
| Linux / WSL | `/etc/claude-code/managed-settings.json` |
| Windows | `C:\Program Files\ClaudeCode\managed-settings.json` |

Reference: [Claude Code settings — settings files](https://code.claude.com/docs/en/settings#settings-files).

## Toggle forced-gateway login while testing (macOS)

Because the managed settings file forces every Claude Code session onto the gateway,
it is handy to switch it on and off while testing. The file is root-owned, so these
use `sudo`. Restart Claude Code after changing it.

```bash
# OFF — restore normal Claude Code login (move the policy aside)
sudo mv "/Library/Application Support/ClaudeCode/managed-settings.json" \
        "/Library/Application Support/ClaudeCode/managed-settings.json.bak"

# ON — re-enable forced gateway login
sudo mv "/Library/Application Support/ClaudeCode/managed-settings.json.bak" \
        "/Library/Application Support/ClaudeCode/managed-settings.json"
```

Verify which settings sources are active with `/status` (look for
`Enterprise managed settings (file)`) or `claude doctor`.

## Post-deploy smoke tests

Run these from a machine that uses the private network and private DNS path:

```bash
dig claude-gateway.corp.example.com
curl https://claude-gateway.corp.example.com/healthz
curl https://claude-gateway.corp.example.com/readyz
curl https://claude-gateway.corp.example.com/protocol
```

The `dig` result must contain only private addresses. If the hostname returns a
public IP, Claude Code gateway login can fail before it reaches the gateway.

Then start Claude Code and run `/login`. The Cloud gateway path should use your
gateway URL, open the Cognito login flow, and complete the gateway session.

## Troubleshooting

**`Gateway hosts must be on your organization's private network`**
- The hostname resolved to at least one public IP from the Claude Code machine.
- Fix DNS first (see [`dns.md`](dns.md)). Do not point `gatewayHost` at CloudFront.

**`curl: Could not resolve host`**
- The developer machine is not using the VPC resolver or corporate conditional
  forwarding.
- Check Client VPN DNS settings, Route 53 Resolver inbound endpoint forwarding, or
  devbox VPC DNS settings.

**`curl` times out on `443`**
- Check `allowedClientCidrs`, Client VPN routes, authorization rules, subnet route
  tables, and the ALB security group ingress.

**`/readyz` fails but `/healthz` works**
- The gateway process is alive, but a dependency such as PostgreSQL, OIDC discovery,
  or migrations is not ready.
- Check ECS task logs in the `LogGroupName` stack output.

**Bedrock calls fail with authorization errors**
- Confirm model access is enabled in Bedrock.
- Confirm the ECS task role policy includes the model or inference-profile ARN you
  intend to use.
