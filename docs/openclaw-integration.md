# OpenClaw Integration

ECS should call webdock through Tailscale.

Recommended ECS bridge environment:

```env
WEB_DOCK_BASE_URL=http://<laptop_tailscale_ip>:18000/v1
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

If webdock is offline, timed out, busy, or not logged in, the ECS bridge should return a short fallback message instead of blocking OpenClaw.
