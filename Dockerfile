# ============================================================
# Stage 1: 前端构建
# ============================================================
FROM node:22-alpine AS frontend-builder

WORKDIR /app/web

# 安装前端依赖
COPY web/package.json web/package-lock.json* ./
RUN npm ci

# 复制前端源码并构建
COPY web/ ./
RUN npm run build

# ============================================================
# Stage 2: Python 运行时
# ============================================================
FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# 安装锁定后的 Python 依赖
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# 复制后端源码
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/docker-entrypoint.sh /app/scripts/docker-entrypoint.sh
COPY run.py ./

RUN chmod 0755 /app/scripts/docker-entrypoint.sh

# Core image excludes optional extension packages, but keeps Git-backed Plugin
# management available. Desired enable state comes from the writable config mount.

# 从构建阶段复制前端产物
COPY --from=frontend-builder /app/web/dist ./web/dist

# 创建数据持久化目录
RUN mkdir -p \
    /app/logs \
    /app/data/sessions \
    /app/data/workspaces \
    /app/data/skills \
    /app/data/rules \
    /app/data/script-library \
    /app/data/workflows

# 暴露端口
EXPOSE 8020

# 健康检查（FastAPI /docs 始终可用）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8020/docs || exit 1

# 生产启动：Gunicorn + UvicornWorker
# --workers 1：项目有状态初始化（MCP/Session/Graph），不支持多 worker
# --timeout 600：Agent 工作流节点最长可执行 10 分钟
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["gunicorn", "src.web_server:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8020", \
     "--workers", "1", \
     "--timeout", "600", \
     "--graceful-timeout", "30", \
     "--keep-alive", "5", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
