# Ubuntu Laptop Setup

Use Ubuntu 24.04 LTS on the idle laptop. Disable sleep and keep the machine on AC power.

## Install

```bash
sudo apt-get update
sudo apt-get install -y git curl
git clone https://github.com/huozao/webdock.git /opt/webdock
cd /opt/webdock
sudo bash scripts/install-ubuntu.sh
```

Edit runtime values:

```bash
sudo nano /opt/webdock/deploy/laptop/.env
```

Set at minimum:

```env
API_TOKEN=replace_with_long_random_api_token
VNC_PASSWORD=changeme
HOST_API_BIND=127.0.0.1
HOST_API_PORT=18000
HOST_NOVNC_BIND=127.0.0.1
HOST_NOVNC_PORT=6080
```

Use an 8-character-or-shorter `VNC_PASSWORD`; VNC commonly truncates longer passwords.

## Start

```bash
sudo systemctl enable --now webdock
bash /opt/webdock/scripts/healthcheck.sh
```

Open:

```text
http://127.0.0.1:6080/vnc.html
```

Log in to ChatGPT in the noVNC browser, then attach:

```bash
source /opt/webdock/deploy/laptop/.env
curl -X POST http://127.0.0.1:18000/browser/attach \
  -H "Authorization: Bearer ${API_TOKEN}"
```

## Keep It Awake

For Ubuntu Desktop:

```bash
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
```

For server installs, verify:

```bash
systemctl status sleep.target suspend.target hibernate.target hybrid-sleep.target
```
