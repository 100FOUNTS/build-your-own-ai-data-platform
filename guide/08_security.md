# Module 8 — Security: identity (OIDC), permissions, secrets and threats

⏱ 6–8 h · Services: `make up-db up-stream up-auth` · Check: `make check-8`
**Tools:** OAuth 2.0, OIDC, IAM, secrets, auditing, *threat modelling* — **Keycloak** (identity) and **OpenBao** (secrets; the open fork of Vault).

## What you will build
1. **Authentication and authorization** in the API and the gateway: they only accept valid Keycloak JWTs and enforce roles (*operator*, *engineer*, *admin*).
2. **Secrets management** with OpenBao: the API gets its connection string with a **least-privilege application identity (AppRole)**, with no `DATABASE_URL` in the environment or in the code.
3. A **STRIDE threat model** of the system, with honest residual risks.

## Why this module exists
Security is not “added at the end”: it is a set of design decisions. Almost every real incident falls into three categories: **leaked credentials** (a
secret in git), **badly validated authentication** (accepting a token from another application or without a signature) and **too many permissions** (an operator who can delete). Here you practice
exactly those three, and learn to *think* like an attacker with STRIDE before a real one does.

## Concepts
- **OAuth 2.0 vs OIDC**: OAuth 2.0 delegates *authorization* (access to resources); **OIDC** adds *identity* (who you are) on top of OAuth. A **JWT access token** carries the identity and the roles, signed.
- **Validating a JWT** = checking the **signature** (with the issuer's public keys: **JWKS**), **`iss`** (issuer), **`aud`** (was it issued *for this API*?), **`exp`** (expiry) and only accepting the **expected algorithms** (RS256; never `none`).
- **401 vs 403**: 401 = I don't know who you are (or your token is no good); 403 = I know who you are but you can't.
- **RBAC**: permissions by role. Roles in Keycloak: `operator` (reads), `engineer` (reads and creates work orders/alerts), `admin` (everything, including deleting). Practice users: `ana`, `bruno`, `carla` (password = their name; development only).
- **Secrets**: never in code, images or git (it stays in the history forever). A manager (OpenBao) hands them to **authenticated** identities, with **least-privilege policies** and rotation.
- **AppRole**: a machine identity for an application: `role_id` (public-ish) + `secret_id` (secret, short-lived).
- **STRIDE**: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege.

## The contract
**API** (`AUTH_DISABLED` ≠ `1`): `/health` is public; everything else requires `Authorization: Bearer <JWT>`.
Environment: `KEYCLOAK_URL` (`http://localhost:8089`), `KEYCLOAK_REALM` (`plant`), `KEYCLOAK_ISSUER` (optional: when the public issuer differs from the internal URL, as in Kubernetes). **Required audience: `plant-api`.**
- No token, a token with a forged signature, no signature (`alg: none`), signed with HS256, for another audience or garbage → **401** with `WWW-Authenticate: Bearer`.
- Roles from `realm_access.roles`: **GET** any role · **POST /work-orders** and **POST /alerts**: `engineer` or `admin` (otherwise **403**) · **DELETE /work-orders/{wo_id}** (new): `admin` only (204; 404 if it doesn't exist) · **GET /me**: `{username, roles}`.
- `AUTH_DISABLED=1` (local development only, off by default) still skips authentication.

**Gateway**: `GET /events?access_token=<jwt>` validates the same way (the browser's EventSource can't send headers); no token or an invalid one → 401. `/healthz` is public.

**OpenBao** (`OPENBAO_ADDR`, admin token `root` in development):
- `scripts/bao_setup.py` (idempotent): stores the secret `url` in `secret/<BAO_SECRET_PATH>` (default `plant/db`) with the value of `DATABASE_URL`; creates a **policy** that allows **only `read`** on `secret/data/<path>`; enables **AppRole** and creates the role `BAO_APP_NAME` (default `plant-api`);
  writes `BAO_ROLE_ID` and `BAO_SECRET_ID` to the file `SECRETS_FILE` (default `.secrets.local`, **in `.gitignore`**).
- `services/common/secrets.py`: `get_database_url()` — if `BAO_ROLE_ID` and `BAO_SECRET_ID` are set it logs in with AppRole and reads the secret; otherwise it uses `DATABASE_URL`. The API starts **without** `DATABASE_URL` in the environment.
- **No** development credentials (`plant:plant@…`, `lakesecret`, `plant-api-secret`, `password="…"`) in `services/ pipelines/ ml/ genai/ scripts/ db/ deploy/ infra/`.

**Threat model** `docs/threat-model.md`: sections *Scope*, *Trust boundaries*, *STRIDE analysis*, *Residual risks*; a table with rows `| T-01 | Component | Threat | STRIDE | Mitigation | Status |`
(≥ 10 threats, all **6** categories covered, ≥ 4 “Implemented” with the real control, concrete mitigations) and ≥ 3 residual risks.

## Your task
1. Log into the Keycloak console (http://localhost:8089, admin/admin) and look at the `plant` realm: clients, roles, mappers. Get a token with `curl` (`password` grant with the `plant-cli` client) and **decode the JWT** (jwt.io or `python -c`): where are `iss`, `aud`, the roles?
2. Write `services/api/app/auth.py`: a dependency that validates the token with `PyJWT` + `PyJWKClient` and another one, `require_roles(...)`. Apply them to the routes.
3. **Forge tokens yourself** (your own RSA key, `alg: none`, another audience with the `client_credentials` token of the `plant-api` client) and check that your API rejects them.
4. Do the same in the gateway (the `jose` library in Node).
5. OpenBao: `scripts/bao_setup.py` and `secrets.py`. Test **least privilege**: with the AppRole try to read *another* secret and to write your own: both must give 403.
6. Sweep the repository for credentials; add `.secrets.local` to `.gitignore`.
7. Write the threat model **walking through the architecture component by component** and asking STRIDE of each one. Be honest about what you didn't solve.

## How you will know you are done
`make check-8` starts your API with real authentication against your Keycloak, gets tokens for `ana`/`bruno`/`carla`, **forges fake tokens of four kinds** and one for another audience, checks every permission and the 401/403/404/204 behavior;
it checks your gateway; runs your `bao_setup.py` twice, logs in with the AppRole and checks least privilege (reading its own: yes; reading someone else's or writing: no); starts the API **without** `DATABASE_URL`; scans the code for credentials; and reviews your STRIDE model.

## Comprehension questions
1. Why check `aud` if the signature is already valid? Describe the attack it prevents.
2. Which attack does accepting `HS256` tokens enable when you expect `RS256`? (*algorithm confusion*)
3. The AppRole `secret_id` is a secret: how is it delivered to the application without writing it in git? (look at how Kubernetes and CI pipelines solve it)
4. The gateway accepts the token in the URL. What risk does that introduce? How would you mitigate it better? (it is in your threat model)
5. Pick an “Implemented” threat from your table: which **automatic** test proves the control works?

## Hints
<details><summary>JWT validation</summary>

`PyJWKClient(f"{KEYCLOAK_URL}/realms/plant/protocol/openid-connect/certs").get_signing_key_from_jwt(token)` looks up the key by `kid`; then `jwt.decode(token, key.key, algorithms=["RS256"], audience="plant-api", issuer=…, options={"require": ["exp","iss","aud"]})`.
</details>
<details><summary>Roles</summary>

Keycloak puts the realm roles in `claims["realm_access"]["roles"]`. Create a FastAPI dependency that returns the claims and another one that compares them with the allowed roles.
</details>
<details><summary>OpenBao with hvac</summary>

`hvac.Client(url, token="root")` → `secrets.kv.v2.create_or_update_secret(path, secret, mount_point="secret")`, `sys.create_or_update_policy(name, 'path "secret/data/…" { capabilities = ["read"] }')`, `sys.enable_auth_method("approle")`, `auth.approle.create_or_update_approle(...)`.
</details>
<details><summary>Writing useful threats</summary>

“An attacker uses a token from another application” (S) → control: `aud` validation. “An operator deletes work orders” (E) → RBAC. “Credentials in git” (I) → OpenBao + scanning. “A client floods the API” (D) → rate limiting. A good threat names the attacker, the resource and the control.
</details>

## Common traps
- Validating only the signature (forgetting `iss`, `aud`, `exp`).
- Trusting roles that come in the request **body** or in an unverified token.
- Returning 403 when the token is invalid (it must be 401).
- Writing the `secret_id` in `compose.yaml`, in a committed `.env` or in a log.
- A threat model with 12 “Implemented” threats and no residual risk: nobody believes it.

## On a real job
Automatic secret rotation, dynamic secrets (short-lived database credentials), mTLS between services, short-lived tokens with *refresh*, centralized auditing and secret scanning in CI.
**Courses:** an introduction to OAuth2/OIDC and the IAM/security part of the AWS Cloud Practitioner course (no free course covers the whole area).

→ Next: [`09_frontend.md`](09_frontend.md)
