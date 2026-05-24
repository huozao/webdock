# webdock

`webdock` runs browser-heavy automation on an Ubuntu laptop instead of the small ECS host.

The first service is a ChatGPT browser relay:

- A normal bundled Chromium process stays open inside a Docker container.
- noVNC lets you manually log in to ChatGPT.
- FastAPI attaches to the same browser through local CDP.
- `/v1/chat/completions` exposes a minimal OpenAI-compatible text API for OpenClaw.

## Recommended Topology

```text
WeChat -> ECS OpenClaw -> ECS bridge -> Tailscale -> Ubuntu laptop webdock -> browser ChatGPT
```

Keep `18000` and `6080` private. Prefer Tailscale between ECS and the laptop. Do not expose noVNC to the public internet.

## Ubuntu Laptop Quick Start

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
