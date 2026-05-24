FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99
ENV DISPLAY_WIDTH=1366
ENV DISPLAY_HEIGHT=768
ENV DISPLAY_DEPTH=24

WORKDIR /app

RUN sed -i \
    -e 's|http://archive.ubuntu.com/ubuntu|https://mirrors.aliyun.com/ubuntu|g' \
    -e 's|http://security.ubuntu.com/ubuntu|https://mirrors.aliyun.com/ubuntu|g' \
    /etc/apt/sources.list \
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

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN ln -sf "$(find /ms-playwright -path '*/chrome-linux/chrome' -type f | head -n 1)" /usr/bin/webdock-chrome

COPY src/ src/
COPY scripts/ scripts/
COPY docker/ docker/
COPY deploy/laptop/.env.example .env.example

RUN mkdir -p /app/browser_data /app/logs/debug /app/.vnc \
    && chmod +x /app/docker/entrypoint.sh

EXPOSE 8000 6080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
