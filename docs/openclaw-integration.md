# OpenClaw Integration

ECS should call webdock through a reverse SSH tunnel from the laptop to ECS. This keeps the webdock API private and does not require ECS to join Tailscale.

Recommended ECS bridge environment:

```env
WEB_DOCK_BASE_URL=http://127.0.0.1:11800/v1
WEB_DOCK_API_TOKEN=replace_with_long_random_api_token
WEB_DOCK_MODEL=browser-chatgpt
WEB_DOCK_TIMEOUT_SECONDS=180
```

The bridge should call:

```text
POST ${WEB_DOCK_BASE_URL}/chat/completions
Authorization: Bearer ${WEB_DOCK_API_TOKEN}
```

Minimal request:

```json
{
  "model": "browser-chatgpt",
  "messages": [
    {
      "role": "user",
      "content": "用户微信消息"
    }
  ],
  "stream": false
}
```

To keep test WeChat accounts A/B/C separate while sharing one ChatGPT login, pass metadata with each request:

```json
{
  "model": "browser-chatgpt",
  "messages": [{"role": "user", "content": "用户微信消息"}],
  "metadata": {
    "wechat_account": "A",
    "chat_type": "private",
    "peer_id": "user-1",
    "chatgpt_project": "WeChat-A"
  },
  "stream": false
}
```

WebDock maps this to a lane key like `wechat:A:private:user-1`. Messages in one lane are serialized. Different lanes can run concurrently up to `MAX_CONCURRENT_CHATS`.

If webdock is offline, timed out, busy, or not logged in, the ECS bridge should return a short fallback message instead of blocking OpenClaw.

## Laptop Reverse Tunnel

Create a dedicated SSH key on the laptop and add its public key to ECS `/root/.ssh/authorized_keys` with a restricted reverse-forwarding prefix:

```text
no-pty,no-agent-forwarding,no-X11-forwarding,permitlisten="127.0.0.1:11800" ssh-ed25519 ... webdock-ecs-tunnel
```

Then on the laptop:

```bash
cd /opt/webdock
sudo bash scripts/install-ecs-tunnel.sh
sudo nano deploy/laptop/ecs-tunnel.env
sudo systemctl enable --now webdock-ecs-tunnel
```

The default tunnel maps:

```text
ECS 127.0.0.1:11800 -> laptop 127.0.0.1:18000
```

If `HOST_API_BIND` is set to the laptop Tailscale IP instead of `127.0.0.1`, set `WEBDOCK_LOCAL_BIND` in `ecs-tunnel.env` to that same IP.
