from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_laptop_compose_keeps_ports_private_by_default():
    compose = (ROOT / "deploy/laptop/compose.yml").read_text(encoding="utf-8")

    assert "${HOST_API_BIND:-127.0.0.1}:${HOST_API_PORT:-18000}:8000" in compose
    assert "${HOST_NOVNC_BIND:-127.0.0.1}:${HOST_NOVNC_PORT:-6080}:6080" in compose
    assert "${HOST_FEISHU_NOVNC_BIND:-127.0.0.1}:${HOST_FEISHU_NOVNC_PORT:-6081}:6081" in compose
    assert "ATTACH_ON_START: ${ATTACH_ON_START:-false}" in compose
    assert "${HOST_BROWSER_DATA_DIR:-/var/lib/webdock/browser_data}:/app/browser_data" in compose
    assert "${HOST_LOGS_DIR:-/var/log/webdock}:/app/logs" in compose


def test_laptop_compose_gives_quota_monitor_its_public_addresses():
    """看板与飞书卡片的对外地址必须在 compose 里给默认值。

    ⚠️ 2026-09-08 实测：生产容器是命令行临时传 env 起的，这两个键不在 compose 里，
    照着本文件的 compose 重建容器就会丢——卡片「查看额度历史」链接变空，
    latest/history 下发的 screenshot_url 也失去 /console 前缀。
    """
    compose = (ROOT / "deploy/laptop/compose.yml").read_text(encoding="utf-8")

    assert "QUOTA_PUBLIC_API_PREFIX: ${QUOTA_PUBLIC_API_PREFIX:-/console/quota/api}" in compose
    assert "QUOTA_PUBLIC_LINK: ${QUOTA_PUBLIC_LINK:-https://hydwang.xyz/console/quota/}" in compose
    assert "image: ${QUOTA_IMAGE:-ghcr.io/huozao/ai-quota-monitor:latest}" in compose


def test_laptop_env_example_uses_safe_defaults():
    example = (ROOT / "deploy/laptop/.env.example").read_text(encoding="utf-8")

    for key in (
        "API_TOKEN=replace_with_long_random_api_token",
        "VNC_PASSWORD=changeme",
        "HOST_API_BIND=127.0.0.1",
        "HOST_API_PORT=18000",
        "HOST_NOVNC_BIND=127.0.0.1",
        "HOST_NOVNC_PORT=6080",
        "HOST_FEISHU_NOVNC_BIND=127.0.0.1",
        "HOST_FEISHU_NOVNC_PORT=6081",
        "ATTACH_ON_START=false",
    ):
        assert key in example


def test_scripts_target_laptop_compose_project():
    for name in ("start.sh", "stop.sh", "healthcheck.sh"):
        text = (ROOT / f"scripts/{name}").read_text(encoding="utf-8")
        assert "deploy/laptop/compose.yml" in text
        assert "-p webdock" in text


def test_supervisor_exposes_independent_feishu_display():
    supervisor = (ROOT / "docker/supervisord.conf").read_text(encoding="utf-8")

    assert "[program:xvfb]" in supervisor
    assert "command=/usr/bin/Xvfb :99" in supervisor
    assert "[program:feishu-xvfb]" in supervisor
    assert "command=/usr/bin/Xvfb :100" in supervisor
    assert "-rfbport 5900" in supervisor
    assert "-rfbport 5901" in supervisor
    assert "6080 localhost:5900" in supervisor
    assert "6081 localhost:5901" in supervisor


def test_boot_unit_starts_and_stops_the_same_set_of_services():
    """起停必须对称，否则重启一次就永久少一个容器。

    ⚠️ 2026-09-09 实测：ExecStop 是不带服务名的 `down`（停整个 project），ExecStart 却只
    点名 `webdock`。一次 WSL 重启后 quota-monitor 容器被 down 删掉、再也没被创建出来；
    `restart: unless-stopped` 对**已删除**的容器无效，而 console 页面照样 200、只有数据接口
    502，所以整整一天没人发现。判据落在「三行命令的服务名列表是否一致」这个连接处。
    """
    unit = (ROOT / "deploy/laptop/webdock.service").read_text(encoding="utf-8")

    checked = 0
    for line in unit.splitlines():
        if not line.startswith(("ExecStartPre=", "ExecStart=", "ExecStop=")):
            continue
        # ⚠️ 不是每条 Exec* 都是 compose 命令（还有 require-pinned-images.sh 那条断言）。
        # 不过滤的话它的路径参数会被当成服务名，测试恒红。
        if "docker compose" not in line:
            continue
        checked += 1
        verb = line.rsplit(" -f ", 1)[-1].split("compose.yml", 1)[-1].split()
        # 去掉 compose 自身的动词与开关，剩下的必须为空：任何服务名都会让起停不对称。
        assert [word for word in verb if not word.startswith("-") and word not in
                {"pull", "up", "down"}] == [], line
    # 过滤条件写错时上面的循环会一条都不检查，和「全部通过」在结果上一样。
    assert checked == 3, f"expected pull/up/down, checked {checked}"


def test_quota_monitor_is_gated_by_a_compose_profile():
    """额度采集只在 webdock2 跑，门控放 profile 不放服务名列表。"""
    compose = (ROOT / "deploy/laptop/compose.yml").read_text(encoding="utf-8")
    example = (ROOT / "deploy/laptop/.env.example").read_text(encoding="utf-8")

    assert 'profiles: ["quota"]' in compose
    # 示例文件里留空 = 一台干净的中继默认不跑额度采集；设值是 webdock2 的事。
    assert "\nCOMPOSE_PROFILES=\n" in example


def test_entrypoint_clears_singleton_locks_for_both_profiles():
    """两个 profile 都要清 Singleton*，否则容器一重建那半边就永久起不来。

    ⚠️ 锁里写的是**创建它的容器 hostname**，重建后 Chrome 判定 profile 被「另一台计算机」
    占用并拒绝启动。ChatGPT 侧一直在清所以每次自愈；Feishu 侧从来没清过，2026-09-09
    一次重启就卡住一天（chrome.log 里是 "in use by another Google Chrome process ... on
    another computer"）。
    """
    entrypoint = (ROOT / "docker/entrypoint.sh").read_text(encoding="utf-8")

    assert "/app/browser_data /app/browser_data/feishu-sync" in entrypoint
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        assert f'"$profile/{name}"' in entrypoint


def test_supervisor_owns_the_feishu_browser_and_gates_its_autostart():
    """Feishu Chrome 与它的 :100/5901/6081 归同一处托管，自启由环境变量门控。

    ⚠️ `%(ENV_FEISHU_CHROME_AUTOSTART)s` 展开不到变量时 supervisord **整个起不来**，
    会连 ChatGPT 那半边一起拖死，所以默认值必须写死在镜像里。
    """
    supervisor = (ROOT / "docker/supervisord.conf").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "deploy/laptop/compose.yml").read_text(encoding="utf-8")

    assert "[program:feishu-chrome]" in supervisor
    assert "--user-data-dir=/app/browser_data/feishu-sync" in supervisor
    assert "--remote-debugging-port=9223" in supervisor
    assert "autostart=%(ENV_FEISHU_CHROME_AUTOSTART)s" in supervisor
    assert 'environment=DISPLAY=":100"' in supervisor
    assert "autorestart=true" in supervisor.split("[program:feishu-chrome]", 1)[1]
    assert "ENV FEISHU_CHROME_AUTOSTART=false" in dockerfile
    assert "FEISHU_CHROME_AUTOSTART: ${FEISHU_CHROME_AUTOSTART:-false}" in compose
    # 两个浏览器的代理是两个独立的键，合并会让其中一台走错出口。
    assert "FEISHU_CHROME_PROXY_SERVER: ${FEISHU_CHROME_PROXY_SERVER:-}" in compose
    assert "CHROME_PROXY_SERVER: ${CHROME_PROXY_SERVER:-}" in compose


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
    assert "location = /login" in nginx_template
    assert "return 302 /login?consent=true;" in nginx_template
    assert "files.hydwang.xyz" in nginx_template
    assert "ECS_REMOTE_PORT=15342" in tunnel_env
    assert "GOKAPI_LOCAL_PORT=53842" in tunnel_env
    assert "-R ${ECS_REMOTE_BIND}:${ECS_REMOTE_PORT}:${GOKAPI_LOCAL_BIND}:${GOKAPI_LOCAL_PORT}" in tunnel_service
    assert "gokapi-ecs-tunnel.service" in tunnel_install
    assert "不要把 Gokapi 合并进 WebDock 主容器" in docs
    assert "https://files.hydwang.xyz" in docs


def test_authentik_is_managed_as_independent_deployment_unit():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    compose = (ROOT / "deploy/authentik/compose.yml").read_text(encoding="utf-8")
    env_example = (ROOT / "deploy/authentik/.env.example").read_text(encoding="utf-8")
    deploy_script = (ROOT / "deploy/authentik/deploy.sh").read_text(encoding="utf-8")
    nginx_template = (ROOT / "deploy/authentik/nginx/auth.hydwang.xyz.conf.template").read_text(encoding="utf-8")
    tunnel_env = (ROOT / "deploy/authentik/ecs-tunnel.env.example").read_text(encoding="utf-8")
    tunnel_service = (ROOT / "deploy/authentik/authentik-ecs-tunnel.service").read_text(encoding="utf-8")
    tunnel_install = (ROOT / "deploy/authentik/install-ecs-tunnel.sh").read_text(encoding="utf-8")
    docs = (ROOT / "docs/authentik.md").read_text(encoding="utf-8")

    assert "deploy/authentik/.env" in gitignore
    assert "deploy/authentik/ecs-tunnel.env" in gitignore
    assert "deploy/authentik/postgresql/" in gitignore
    assert "deploy/authentik/data/" in gitignore
    assert "image: ${AUTHENTIK_IMAGE:-ghcr.io/goauthentik/server}:${AUTHENTIK_TAG:-2026.5.3}" in compose
    assert "${AUTHENTIK_BIND:-127.0.0.1}:${COMPOSE_PORT_HTTP:-9000}:9000" in compose
    assert "${AUTHENTIK_POSTGRESQL_DIR:-/var/lib/authentik/postgresql}:/var/lib/postgresql/data" in compose
    assert "${AUTHENTIK_DATA_DIR:-/var/lib/authentik/data}:/data" in compose
    assert "PG_PASS=replace_with_long_random_postgres_password" in env_example
    assert "AUTHENTIK_SECRET_KEY=replace_with_long_random_secret_key" in env_example
    assert "AUTHENTIK_BOOTSTRAP_PASSWORD=replace_with_initial_admin_password" in env_example
    assert "docker compose -p authentik" in deploy_script
    assert "docker compose -p webdock" not in deploy_script
    assert "/-/health/live/" in deploy_script
    assert "proxy_pass http://127.0.0.1:19000;" in nginx_template
    assert "auth.hydwang.xyz" in nginx_template
    assert "ECS_REMOTE_PORT=19000" in tunnel_env
    assert "AUTHENTIK_LOCAL_PORT=9000" in tunnel_env
    assert "-R ${ECS_REMOTE_BIND}:${ECS_REMOTE_PORT}:${AUTHENTIK_LOCAL_BIND}:${AUTHENTIK_LOCAL_PORT}" in tunnel_service
    assert "authentik-ecs-tunnel.service" in tunnel_install
    assert "https://auth.hydwang.xyz" in docs
    assert "Gokapi" in docs
    assert "Google" in docs
    assert "微信" in docs


def test_entrypoint_warns_about_vnc_password_truncation():
    entrypoint = (ROOT / "docker/entrypoint.sh").read_text(encoding="utf-8")

    assert "VNC_PASSWORD is longer than 8 chars" in entrypoint
    assert "${#VNC_PASSWORD}" in entrypoint


def test_supervisord_rpc_channel_is_actually_loadable():
    """supervisorctl 的通道段必须能被 supervisord 解析，否则整个容器起不来。

    ⚠️ 2026-09-09 实测：工厂路径写成 `supervisor.rpcinterface.make_main_rpcinterface`
    （模块与可调用对象之间用了 `.`），supervisord 报
    `cannot be resolved within [rpcinterface:supervisor]` 后立即退出，容器进无限重启循环。
    差一个字符，而当时的测试只断言了各 [program:] 段的字符串，没有任何判据覆盖这一行，
    所以 CI 全绿、镜像照常发布，直到设备上真正起了一次才暴露。
    entry-point 语法要求 `模块:可调用对象`。
    """
    supervisor = (ROOT / "docker/supervisord.conf").read_text(encoding="utf-8")

    assert "[rpcinterface:supervisor]" in supervisor
    assert (
        "supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface"
        in supervisor
    )
    # 三段是一套：少了 unix_http_server 或 supervisorctl，supervisorctl 仍然连不上。
    assert "[unix_http_server]" in supervisor
    assert "file=/tmp/supervisor.sock" in supervisor
    assert "[supervisorctl]" in supervisor
    assert "serverurl=unix:///tmp/supervisor.sock" in supervisor

    # 每个 rpcinterface_factory 都必须是 `模块:对象`，不能退回成全点号写法。
    for line in supervisor.splitlines():
        if line.startswith("supervisor.rpcinterface_factory"):
            value = line.split("=", 1)[1].strip()
            assert ":" in value, line


def test_boot_unit_refuses_to_start_without_the_pinned_images():
    """pull 失败被 `-` 吞掉后，up -d 会先删旧容器再报 No such image。

    ⚠️ 2026-09-09 实测：.env 刚换 tag、设备恰好断网，两个健康容器被删光、服务停 45 分钟。
    判据落在连接处——ExecStart 的前置条件是「pin 的镜像本地拿得到」，不是「pull 跑过了」。
    断言必须排在 pull 之后、up 之前，否则拦不住。
    """
    unit = (ROOT / "deploy/laptop/webdock.service").read_text(encoding="utf-8")
    guard = ROOT / "deploy/laptop/require-pinned-images.sh"

    assert guard.exists()
    assert guard.stat().st_mode & 0o111, "守卫脚本必须可执行，否则 ExecStartPre 直接失败"

    lines = unit.splitlines()
    guard_at = next(i for i, l in enumerate(lines) if "require-pinned-images.sh" in l)
    pull_at = next(i for i, l in enumerate(lines) if l.startswith("ExecStartPre=-"))
    up_at = next(i for i, l in enumerate(lines) if l.startswith("ExecStart=") and " up " in l)

    assert lines[guard_at].startswith("ExecStartPre="), lines[guard_at]
    # 不带前导 `-`：这条失败就必须中止启动，那正是它存在的意义。
    assert not lines[guard_at].startswith("ExecStartPre=-"), lines[guard_at]
    assert pull_at < guard_at < up_at

    # 脚本只读 .env 里需要的键，不 source 整个文件（会把密钥带进环境）。
    script = guard.read_text(encoding="utf-8")
    code = [l for l in script.splitlines() if not l.lstrip().startswith("#")]
    for line in code:
        assert not line.lstrip().startswith(("source ", ". ")), line
    assert "docker image inspect" in script
    # quota 镜像的校验必须跟着 profile 走，否则 webdock1（不设 profile）会被误拦。
    assert "COMPOSE_PROFILES" in script
    assert "WEBDOCK_IMAGE" in script
    assert "QUOTA_IMAGE" in script
