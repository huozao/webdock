# Tailscale Network

Use Tailscale so ECS can call the laptop without opening public ports.

## Laptop

Install Tailscale:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
tailscale ip -4
```

After you know the laptop Tailscale IP, set:

```env
HOST_API_BIND=<laptop_tailscale_ip>
HOST_NOVNC_BIND=127.0.0.1
```

Restart:

```bash
sudo systemctl restart webdock
```

## ECS

Install and join the same tailnet:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale status
```

From ECS, verify:

```bash
curl -fsS http://<laptop_tailscale_ip>:18000/healthz
```

Do not open `18000` or `6080` in Alibaba Cloud security groups.
