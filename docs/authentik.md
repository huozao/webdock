> **DEPRECATED (2026-07):** Authentik 由 aliecs 上的 Authelia+lldap 统一登录替代
> （见 AliECS `docs/superpowers/specs/2026-07-03-unified-account-system-design.md`）。
> 本部署单元待 SSO 收口后删除；退役步骤见 AliECS `docs/sso-cutover-runbook.md`。

# Authentik deployment

Authentik is managed as an independent deployment unit under `deploy/authentik`.
It is not merged into the WebDock application container and should not share
WebDock runtime state.

Public entrypoint:

- `https://auth.hydwang.xyz`

Runtime location on the old laptop:

- Compose project: `authentik`
- HTTP listener: `127.0.0.1:9000`
- PostgreSQL data: `/var/lib/authentik/postgresql`
- Authentik data: `/var/lib/authentik/data`

ECS only terminates TLS and proxies to the reverse SSH tunnel:

- ECS local upstream: `http://127.0.0.1:19000`
- Old laptop local upstream: `http://127.0.0.1:9000`

## Deploy

On the old laptop:

```bash
cd /opt/webdock
cp deploy/authentik/.env.example deploy/authentik/.env
chmod 600 deploy/authentik/.env
```

Generate fresh values for `PG_PASS`, `AUTHENTIK_SECRET_KEY`,
`AUTHENTIK_BOOTSTRAP_PASSWORD`, and `AUTHENTIK_BOOTSTRAP_TOKEN` before first
start. Then run:

```bash
bash deploy/authentik/deploy.sh
sudo bash deploy/authentik/install-ecs-tunnel.sh
```

On ECS, install the `auth.hydwang.xyz` Nginx template and request a Let's
Encrypt certificate for `auth.hydwang.xyz`.

## Gokapi OIDC

Gokapi should use Authentik through OpenID Connect instead of reading AliECS
user tables, JWTs, or sessions. Keep the Gokapi service independent:

- Provider URL: `https://auth.hydwang.xyz/application/o/gokapi/`
- Redirect URL in Authentik: `https://files.hydwang.xyz/oauth-callback`
- Scopes: `openid`, `email`, `profile`, and optionally `groups`
- Admin email: the Authentik user email selected as the Gokapi super-admin

Public Gokapi download links should remain unauthenticated and handled by
Gokapi itself.

## Google and WeChat sources

Google and WeChat are external identity sources for Authentik. They require
OAuth client credentials created in the corresponding provider consoles before
they can be enabled.

Recommended order:

1. Run Authentik with local `akadmin` and MFA first.
2. Connect Gokapi to Authentik with OIDC.
3. Add Google OAuth as a login source after Google client credentials exist.
4. Add 微信 OAuth only after the WeChat Open Platform application and callback
   domain are approved.

Do not store Google or 微信 client secrets in Git.
