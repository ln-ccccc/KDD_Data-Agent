FROM m.daocloud.io/docker.io/library/python:3.11-slim

# 安装系统基础依赖与 curl (用于安装 uv)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv 包管理器
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 设置工作目录
WORKDIR /app

# 优先拷贝依赖配置文件，利用 Docker 缓存加速依赖安装
COPY pyproject.toml uv.lock ./

# 安装依赖项（不包含当前项目代码本身）
RUN uv sync --frozen --no-install-project

# 拷贝全部代码与配置
COPY . .

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

# 默认运行命令（可通过 docker-compose 或 run 参数覆盖）
ENTRYPOINT ["uv", "run", "dabench"]
