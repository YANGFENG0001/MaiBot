# Runtime image
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Working directory
WORKDIR /MaiMBot

ENV MAIBOT_LEGACY_0X_UPGRADE_CONFIRMED=1
ENV MAIBOT_WEBUI_USE_LOCAL_DASHBOARD=1
ENV PATH="/MaiMBot/.venv/bin:${PATH}"

# Copy dependency metadata
COPY pyproject.toml uv.lock ./

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies
# 依赖统一走 pyproject.toml 里配置的 aliyun 镜像（index-strategy = unsafe-first-match
# 会优先命中它）。从 GitHub 的 runner 拉大 wheel（faiss-cpu、maibot-dashboard）时
# 偶发读超时，默认 UV_HTTP_TIMEOUT=30s / UV_HTTP_RETRIES=3 会让整次构建直接失败
# （2026-10-07 实测过一次，重跑即过）。这里放宽，避免偶发慢速把镜像构建打红。
ENV UV_HTTP_TIMEOUT=300 \
    UV_HTTP_RETRIES=10

RUN uv sync --locked --no-dev --no-install-project

# Install system libraries required by Playwright Chromium. The browser binary
# itself is downloaded lazily into the configured data directory at runtime.
RUN python -m playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Copy project source. Release workflows build dashboard/dist before this step,
# so the runtime image serves a WebUI from the same source revision as the backend.
COPY . .

# Bundle the adapter plugin that matches the shipped protocol implementation.
# 目录名即插件在 /MaiMBot/plugins 下的落地名，entrypoint 会整体复制过去。
RUN git clone --depth 1 --branch main https://github.com/Mai-with-u/MaiBot-SnowLuma-Adapter.git plugin-templates/snowluma-adapter
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000 8001

ENTRYPOINT [ "./docker-entrypoint.sh" ]
