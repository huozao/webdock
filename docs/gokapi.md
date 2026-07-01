# Gokapi 文件中转部署

Gokapi 作为独立部署单元运行在旧电脑 `webdock1`，主入口域名是 `https://files.hydwang.xyz`。

不要把 Gokapi 合并进 WebDock 主容器。WebDock 主容器负责 Chrome、noVNC、Playwright 和 ChatGPT 登录态；Gokapi 负责文件中转、公开下载链接、过期时间、下载次数和原生管理页面。

## 文件边界

- `deploy/gokapi/compose.yml`：Gokapi 独立 Compose 服务。
- `deploy/gokapi/.env.example`：非密钥示例配置。
- `deploy/gokapi/.env`：本机实际配置，不提交。
- `deploy/gokapi/deploy.sh`：拉取镜像、重建容器、健康检查。
- `deploy/gokapi/nginx/files.hydwang.xyz.conf.template`：反代模板。

## 首次部署

```bash
cd /opt/webdock
cp deploy/gokapi/.env.example deploy/gokapi/.env
nano deploy/gokapi/.env
bash deploy/gokapi/deploy.sh
```

首次启动后，打开 `http://127.0.0.1:53842/setup` 或反代后的 `https://files.hydwang.xyz/setup` 完成 Gokapi 原生初始化。

## 升级

生产建议固定 `GOKAPI_IMAGE` 到明确 tag。升级时只改 `deploy/gokapi/.env` 或提交更新 `.env.example` 的推荐 tag，然后执行：

```bash
cd /opt/webdock
bash deploy/gokapi/deploy.sh
```

数据目录和配置目录独立挂载到 `/app/data`、`/app/config`，容器升级不会删除已有文件和配置。需要回滚时，把 `GOKAPI_IMAGE` 改回上一版再运行部署脚本。

## 反代

`deploy/gokapi/nginx/files.hydwang.xyz.conf.template` 默认代理到 `http://127.0.0.1:53842`。如果 Nginx 不在 Gokapi 同一台机器上，把 `proxy_pass` 改成旧电脑可达地址或隧道本地端口。
