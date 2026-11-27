# Set up Okta

You need an Okta org where you are a super administrator. A free trial or Integrator org works. All steps are in the
Okta Admin Console (`https://<your-org>-admin.okta.com`).

## 1. Groups

**Directory > Groups > Add group**: create `ocgw-team-a` and `ocgw-team-b`. These names match the default
`group_models` in Terraform; if you use other names, change `group_models` to match.

## 2. Users

**Directory > People > Add person**: add the people who will use the gateway and add each one to a group. For a test,
three users are enough: one in `ocgw-team-a`, two in `ocgw-team-b` (the test suite gives the third a tiny budget).

## 3. The native app

**Applications > Applications > Create App Integration**

| Setting | Value |
|---|---|
| Sign-in method | OIDC - OpenID Connect |
| Application type | Native Application |
| App integration name | opencode gateway |
| Grant type | Authorization Code, Refresh Token |
| Sign-in redirect URIs | `http://127.0.0.1:8765/callback` |
| Sign-out redirect URIs | (none) |
| Assignments | Limit access to selected groups: `ocgw-team-a`, `ocgw-team-b` |

On the app's **General** tab, under **Client Credentials**, keep **Client authentication: None** and **Require PKCE**
checked. Note the **Client ID**.

Under **Refresh Token** choose **Rotate token after every use** and keep the grace period at 30 seconds: `ocgw` saves
the rotated token and serialises refreshes with a file lock.

## 4. Groups claim

**Security > API > Authorization Servers > default > Claims > Add Claim**

| Setting | Value |
|---|---|
| Name | `groups` |
| Include in token type | ID Token, Always |
| Value type | Groups |
| Filter | Starts with `ocgw-` |
| Include in | Any scope |

`ocgw` requests `openid email profile offline_access`. It does not request a `groups` scope, because the `default`
authorization server has none; the claim above is included in any scope instead. If you add a `groups` scope of your
own, set `"scope"` in the ocgw config to include it. Use **Token Preview** on the authorization server, with your app
and a test user, to check that the ID token has `sub` and `groups`.

## 5. Terraform settings

```hcl
idp                   = "okta"
oidc_discovery_url    = "https://<your-org>.okta.com/oauth2/default/.well-known/openid-configuration"
oidc_client_id        = "<Client ID>"
oidc_allowed_audience = ["<Client ID>"]   # the ID token's aud is the client ID
user_claim            = "sub"
group_claim           = "groups"

# Each person who may use the gateway: a ledger user (budget, usage project, rate limits) with a group, and the
# IdP user ID that signs in as them. Anyone whose ID is not in identity_map is refused.
users        = { alice = "ocgw-team-a", bob = "ocgw-team-b" }
identity_map = { "<alice's user ID>" = "alice", "<bob's user ID>" = "bob" }
```

The user ID is the `00u...` ID in the URL of the user under **Directory > People**; it is the ID token's `sub`. The sample keys on `sub` rather than `email` because a user
cannot change it, so nobody can sign in under someone else's budget by editing a profile field.

## 6. ocgw settings

```json
{"issuer": "https://<your-org>.okta.com/oauth2/default", "client_id": "<Client ID>", "token_kind": "id_token",
 "gateway_url": "https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com"}
```
