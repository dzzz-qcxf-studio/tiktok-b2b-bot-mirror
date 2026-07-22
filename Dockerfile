# ===== Stage 1: Build UI =====
FROM node:20-alpine AS ui-builder
WORKDIR /app/ui
COPY tiktok_bot_console/ui/package*.json ./
RUN npm ci
COPY tiktok_bot_console/ui/ ./
RUN npm run build

# ===== Stage 2: Runtime =====
FROM python:3.12-slim

# Playwright 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 libxshmfence1 nginx \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python 依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    sqlalchemy aiosqlite chromadb openai pydantic pydantic-settings \
    python-dotenv python-telegram-bot matplotlib click fastapi uvicorn \
    python-multipart rich playwright -i https://pypi.tuna.tsinghua.edu.cn/simple

RUN playwright install chromium && playwright install-deps chromium

# 应用代码
COPY tiktok_bot_core/ tiktok_bot_core/
COPY tiktok_bot_api/ tiktok_bot_api/
COPY tiktok_bot_console/cli/ tiktok_bot_console/cli/

# UI 静态文件
COPY --from=ui-builder /app/ui/dist /app/ui-dist
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

# 数据目录
RUN mkdir -p /app/data /app/reports /app/logs

# 启动脚本
COPY docker/entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
EXPOSE 80

CMD ["/app/entrypoint.sh"]
