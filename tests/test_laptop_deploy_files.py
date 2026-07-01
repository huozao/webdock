from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_laptop_compose_keeps_ports_private_by_default():
    compose = (ROOT / "deploy/laptop/compose.yml").read_text(encoding="utf-8")

    assert "${HOST_API_BIND:-127.0.0.1}:${HOST_API_PORT:-18000}:8000" in compose
    assert "${HOST_NOVNC_BIND:-127.0.0.1}:${HOST_NOVNC_PORT:-6080}:6080" in compose
    assert "ATTACH_ON_START: ${ATTACH_ON_START:-false}" in compose
    assert "${HOST_BROWSER_DATA_DIR:-/var/lib/webdock/browser_data}:/app/browser_data" in compose
    assert "${HOST_LOGS_DIR:-/var/log/webdock}:/app/logs" in compose


def test_laptop_env_example_uses_safe_defaults():
    example = (ROOT / "deploy/laptop/.env.example").read_text(encoding="utf-8")

    for key in (
        "API_TOKEN=replace_with_long_random_api_token",
        "VNC_PASSWORD=changeme",
        "HOST_API_BIND=127.0.0.1",
        "HOST_API_PORT=18000",
        "HOST_NOVNC_BIND=127.0.0.1",
        "HOST_NOVNC_PORT=6080",
        "ATTACH_ON_START=false",
    ):
        assert key in example


def test_scripts_target_laptop_compose_project():
    for name in ("start.sh", "stop.sh", "healthcheck.sh"):
        text = (ROOT / f"scripts/{name}").read_text(encoding="utf-8")
        assert "deploy/laptop/compose.yml" in text
        assert "-p webdock" in text


def test_ecs_tunnel_files_keep_webdock_private():
    env_example = (ROOT / "deploy/laptop/ecs-tunnel.env.example").read_text(encoding="utf-8")
    service = (ROOT / "deploy/laptop/webdock-ecs-tunnel.service").read_text(encoding="utf-8")
    install_script = (ROOT / "scripts/install-ecs-tunnel.sh").read_text(encoding="utf-8")

    assert "ECS_REMOTE_BIND=127.0.0.1" in env_example
    assert "ECS_REMOTE_PORT=11800" in env_example
    assert "WEBDOCK_LOCAL_PORT=18000" in env_example
    assert "-R ${ECS_REMOTE_BIND}:${ECS_REMOTE_PORT}:${WEBDOCK_LOCAL_BIND}:${WEBDOCK_LOCAL_PORT}" in service
    assert "webdock-ecs-tunnel.service" in install_script


def test_mihomo_proxy_files_use_local_secret_env():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    env_example = (ROOT / "deploy/laptop/mihomo.env.example").read_text(encoding="utf-8")
    template = (ROOT / "deploy/laptop/mihomo-config.yaml.template").read_text(encoding="utf-8")
    install_script = (ROOT / "scripts/install-mihomo-proxy.sh").read_text(encoding="utf-8")

    assert "deploy/laptop/mihomo.env" in gitignore
    assert "MIHOMO_SUBSCRIPTION_URL=" in env_example
    assert "MIHOMO_BIND_ADDRESS=172.17.0.1" in env_example
    assert "token=" not in env_example
    assert "bind-address: ${MIHOMO_BIND_ADDRESS}" in template
    assert "proxy-providers:" in template
    assert "backup-subscription" in template
    assert "type: fallback" in template
    assert "-t -d" in install_script


def test_gokapi_is_managed_as_independent_deployment_unit():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    compose = (ROOT / "deploy/gokapi/compose.yml").read_text(encoding="utf-8")
    env_example = (ROOT / "deploy/gokapi/.env.example").read_text(encoding="utf-8")
    deploy_script = (ROOT / "deploy/gokapi/deploy.sh").read_text(encoding="utf-8")
    nginx_template = (ROOT / "deploy/gokapi/nginx/files.hydwang.xyz.conf.template").read_text(encoding="utf-8")
    tunnel_env = (ROOT / "deploy/gokapi/ecs-tunnel.env.example").read_text(encoding="utf-8")
    tunnel_service = (ROOT / "deploy/gokapi/gokapi-ecs-tunnel.service").read_text(encoding="utf-8")
    tunnel_install = (ROOT / "deploy/gokapi/install-ecs-tunnel.sh").read_text(encoding="utf-8")
    docs = (ROOT / "docs/gokapi.md").read_text(encoding="utf-8")

    assert "deploy/gokapi/.env" in gitignore
    assert "deploy/gokapi/ecs-tunnel.env" in gitignore
    assert "deploy/gokapi/data/" in gitignore
    assert "deploy/gokapi/config/" in gitignore
    assert "image: ${GOKAPI_IMAGE:-f0rc3/gokapi:latest}" in compose
    assert "${GOKAPI_BIND:-127.0.0.1}:${GOKAPI_HOST_PORT:-53842}:53842" in compose
    assert "${GOKAPI_DATA_DIR:-/var/lib/gokapi/data}:/app/data" in compose
    assert "${GOKAPI_CONFIG_DIR:-/var/lib/gokapi/config}:/app/config" in compose
    assert "GOKAPI_IMAGE=f0rc3/gokapi:latest" in env_example
    assert "GOKAPI_BIND=127.0.0.1" in env_example
    assert "docker compose -p gokapi" in deploy_script
    assert "docker compose -p webdock" not in deploy_script
    assert "proxy_pass http://127.0.0.1:15342;" in nginx_template
    assert "files.hydwang.xyz" in nginx_template
    assert "ECS_REMOTE_PORT=15342" in tunnel_env
    assert "GOKAPI_LOCAL_PORT=53842" in tunnel_env
    assert "-R ${ECS_REMOTE_BIND}:${ECS_REMOTE_PORT}:${GOKAPI_LOCAL_BIND}:${GOKAPI_LOCAL_PORT}" in tunnel_service
    assert "gokapi-ecs-tunnel.service" in tunnel_install
    assert "不要把 Gokapi 合并进 WebDock 主容器" in docs
    assert "https://files.hydwang.xyz" in docs


def test_entrypoint_warns_about_vnc_password_truncation():
    entrypoint = (ROOT / "docker/entrypoint.sh").read_text(encoding="utf-8")

    assert "VNC_PASSWORD is longer than 8 chars" in entrypoint
    assert "${#VNC_PASSWORD}" in entrypoint
