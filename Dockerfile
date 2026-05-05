FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim

# 安装系统基础依赖与 curl (用于安装 uv)
# 如果 apt-get 在国内太慢，我们还可以替换为阿里云镜像源
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 方案二：不再依赖多阶段构建，直接在当前镜像内部通过官方脚本安装 uv
# 这避开了从外部 registry 拉取 ghcr.io 镜像时的超时和 403 问题
# 为了加速 uv 安装包，我们设置代理
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/bin" sh

# 设置工作目录
WORKDIR /app

# 优先拷贝依赖配置文件，利用 Docker 缓存加速依赖安装
COPY pyproject.toml uv.lock ./

# 安装依赖项（不包含当前项目代码本身）
RUN uv sync --frozen --no-install-project

# 拷贝全部代码与配置
COPY . .

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# 完整安装项目
RUN uv sync --frozen

# 【核心步骤】预下载 DuckDB 扩展至镜像内
# 避免运行时因为沙箱无网络、或者并发抢占文件锁导致的死锁和超时
RUN uv run python -c "\
import duckdb; \
conn=duckdb.connect(); \
conn.execute(\"SET extension_directory='/app/.duckdb_extensions'\"); \
conn.execute(\"INSTALL json; INSTALL sqlite; INSTALL excel;\"); \
"

# Default startup: run evaluation loop (no args). For local debugging, pass args to run dabench subcommands.
ENTRYPOINT ["/app/entrypoint.sh"]
