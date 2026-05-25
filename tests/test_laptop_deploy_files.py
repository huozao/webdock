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


def test_entrypoint_warns_about_vnc_password_truncation():
    entrypoint = (ROOT / "docker/entrypoint.sh").read_text(encoding="utf-8")

    assert "VNC_PASSWORD is longer than 8 chars" in entrypoint
    assert "${#VNC_PASSWORD}" in entrypoint
