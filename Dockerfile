FROM python:3.12-slim

WORKDIR /app

# 系统依赖（Playwright 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制依赖文件
COPY pyproject.toml .
RUN uv sync --frozen || uv sync

# 复制应用代码
COPY . .

# Playwright 浏览器（rmfyalk 爬虫需要）
RUN uv run playwright install chromium --with-deps

# 预下载 embedding 模型（可选，加快首次启动）
RUN uv run python -c "from embed import _get_model; _get_model()" || true

ENV CASE_DB_PATH=/data
RUN mkdir -p /data

EXPOSE 8080

CMD ["uv", "run", "python", "server.py", "--transport", "http", "--host", "0.0.0.0", "--port", "8080"]
