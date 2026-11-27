# Set up JumpCloud

All steps are in the JumpCloud Admin Portal (`https://console.jumpcloud.com`).

## 1. User groups and users

**User Management > User Groups > +**: create `ocgw-team-a` and `ocgw-team-b`. Add the users who will use the gateway.

## 2. The OIDC app

**SSO Applications > + Add New Application > Custom Application**, then choose **Manage Single Sign-On (SSO)** and
**Configure SSO with OIDC**.

| Setting | Value |
|---|---|
| Redirect URIs | `http://127.0.0.1:8765/callback` |
| Client Authentication Type | Public (None PKCE) |
| Grant types | Authorization Code, Refresh Token |
| Login URL | `http://127.0.0.1:8765/` (not used, but the form requires it) |
| Attribute mapping | Standard scopes: Email, Profile |
| Group attributes | Include group attribute: on, name `groups` |
| User groups (access) | `ocgw-team-a`, `ocgw-team-b` |

Save and note the **Client ID**.

## 3. Terraform settings

```hcl
idp                   = "jumpcloud"
oidc_discovery_url    = "https://oauth.id.jumpcloud.com/.well-known/openid-configuration"
oidc_client_id        = "<Client ID>"
oidc_allowed_audience = ["<Client ID>"]
user_claim            = "sub"
group_claim           = "groups"

# Each person who may use the gateway: a ledger user (budget, usage project, rate limits) with a group, and the
# IdP user ID that signs in as them. Anyone whose ID is not in identity_map is refused.
users        = { alice = "ocgw-team-a", bob = "ocgw-team-b" }
identity_map = { "<alice's user ID>" = "alice", "<bob's user ID>" = "bob" }
```

The user ID is the ID in the URL of the user under **User Management > Users**; it is the ID token's `sub`. The sample keys on `sub` rather than `email` because a user
cannot change it, so nobody can sign in under someone else's budget by editing a profile field.

## 4. ocgw settings

```json
{"issuer": "https://oauth.id.jumpcloud.com/", "client_id": "<Client ID>", "token_kind": "id_token",
 "gateway_url": "https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com"}
```
