FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99
ENV DISPLAY_WIDTH=1366
ENV DISPLAY_HEIGHT=768
ENV DISPLAY_DEPTH=24
# Feishu 同步浏览器的默认值必须在镜像里给全：supervisord 的 %(ENV_...)s 展开不到变量时
# **整个 supervisord 起不来**，连 ChatGPT 那半边一起死。compose 会覆盖这三个值；
# 直接 docker run 起镜像的场合靠这里兜底。默认不自启，只有 webdock2 打开。
ENV FEISHU_CHROME_AUTOSTART=false
ENV FEISHU_CHROME_URL=https://www.feishu.cn/
ENV FEISHU_CHROME_PROXY_SERVER=

WORKDIR /app

# apt 源默认走官方 archive。⚠️ 之前这里写死了 mirrors.aliyun.com——那只在**国内构建**
# 时有意义，镜像实际是在 GitHub runner（境外）上构建的，从阿里云镜像站拉包反而是负优化，
# 2026-09-08 实测同一份 Dockerfile 三次构建 9 / 34 / 25 分钟，波动主要来自这一层。
# 国内本地构建仍可显式打开：docker build --build-arg APT_MIRROR=https://mirrors.aliyun.com/ubuntu .
ARG APT_MIRROR=""
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i -e "s|http://archive.ubuntu.com/ubuntu|$APT_MIRROR|g" \
             -e "s|http://security.ubuntu.com/ubuntu|$APT_MIRROR|g" \
             /etc/apt/sources.list; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    supervisor \
    curl \
    procps \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Use real Google Chrome instead of bundled Chromium (lower Cloudflare bot signal),
# then point webdock-chrome at it. apt on jammy accepts an ASCII-armored signed-by key,
# so no gnupg is required.
# Keep this BEFORE the requirements.txt/pip layer: Chrome rarely changes, so caching it
# first avoids a ~150MB Google Chrome re-download every time a Python dep changes.
RUN curl -fsSL https://dl.google.com/linux/linux_signing_key.pub -o /usr/share/keyrings/google-chrome.asc \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.asc] https://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/google-chrome-stable /usr/bin/webdock-chrome

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY scripts/ scripts/
COPY docker/ docker/
COPY deploy/laptop/.env.example .env.example

RUN mkdir -p /app/browser_data /app/logs/debug /app/.vnc \
    /etc/opt/chrome/policies/managed \
    && install -m 644 /app/docker/chrome-managed-policy.json /etc/opt/chrome/policies/managed/webdock.json \
    && chmod +x /app/docker/entrypoint.sh

EXPOSE 8000 6080 6081

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
