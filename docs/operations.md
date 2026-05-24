# Operations

## Status

```bash
systemctl status webdock --no-pager
docker compose -p webdock --env-file /opt/webdock/deploy/laptop/.env -f /opt/webdock/deploy/laptop/compose.yml ps
curl -fsS http://127.0.0.1:18000/healthz
```

## Logs

```bash
docker logs --tail 120 webdock
docker exec webdock tail -n 100 /app/logs/api.log
docker exec webdock tail -n 100 /app/logs/chrome.log
```

## Restart

```bash
sudo systemctl restart webdock
```

After restart, the browser should reopen with the persisted profile. You may need to run:

```bash
source /opt/webdock/deploy/laptop/.env
curl -X POST http://127.0.0.1:18000/browser/attach \
  -H "Authorization: Bearer ${API_TOKEN}"
```

## Stop

```bash
sudo systemctl stop webdock
```
