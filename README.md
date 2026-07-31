# webdock

`webdock` runs browser-heavy ChatGPT automation on WebDock worker devices. Current primary/standby, host type, tunnel and validation commands are authoritative only in [`../AliECS/docs/fleet.md`](../AliECS/docs/fleet.md).

The first service is a ChatGPT browser relay:

- A normal bundled Chromium process stays open inside a Docker container.
- noVNC lets you manually log in to ChatGPT.
- FastAPI attaches to the same browser through local CDP.
- `/v1/chat/completions` exposes a minimal OpenAI-compatible text API for OpenClaw.
- Requests can be routed into stable `wechat_account + chat_type + peer_id` lanes so test WeChat accounts A/B/C can keep separate ChatGPT pages while sharing one logged-in ChatGPT profile.
- `/storage/photos` stores Couple Memory original photos on the old laptop for AliECS.

## Logical Topology

```text
message channel -> OpenClaw/bridge -> failover proxy -> selected WebDock worker -> browser ChatGPT
```

Keep `18000` and `6080` private. Prefer Tailscale between ECS and the laptop. Do not expose noVNC to the public internet.

Couple Memory photos are stored under `HOST_PHOTO_STORAGE_DIR` on the laptop and are accessed by AliECS through the private WebDock API token, not by direct public laptop URLs.

## Navigation

- Repository rules and login red line: [`AGENTS.md`](AGENTS.md)
- Browser/login/Cloudflare troubleshooting: [`docs/runbooks/browser.md`](docs/runbooks/browser.md)
- Device installation and runtime configuration are owned by the `infra` repository.

## Ubuntu Laptop Quick Start (generic; verify with infra first)

```bash
git clone https://github.com/huozao/webdock.git /opt/webdock
cd /opt/webdock
sudo bash scripts/install-ubuntu.sh
sudo nano deploy/laptop/.env
sudo systemctl enable --now webdock
bash scripts/healthcheck.sh
```

Open noVNC from the laptop:

```text
http://127.0.0.1:6080/vnc.html
```

After logging in to ChatGPT:

```bash
source deploy/laptop/.env
curl -X POST http://127.0.0.1:18000/browser/attach \
  -H "Authorization: Bearer ${API_TOKEN}"
```

Test the OpenAI-compatible API:

```bash
curl -s http://127.0.0.1:18000/v1/chat/completions \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"model":"browser-chatgpt","messages":[{"role":"user","content":"你好，用一句话回复我"}],"stream":false}'
```

## Docs

- `docs/ubuntu-laptop-setup.md`
- `docs/tailscale-network.md`
- `docs/openclaw-integration.md`
- `docs/operations.md`

## Chat lanes

WebDock accepts optional OpenAI-compatible request metadata:

```json
{
  "wechat_account": "A",
  "chat_type": "private",
  "peer_id": "user-1",
  "chatgpt_project": "WeChat-A"
}
```

Each lane is serialized internally, while different lanes may run concurrently up to `MAX_CONCURRENT_CHATS` in `deploy/laptop/.env`.
