# Docker 运行指南与防超时优化

本指南介绍了如何通过 Docker 容器化方案运行 KDD Cup Data Agent Baseline。

## 为什么我们需要 Docker？

在直接使用物理机或多进程本地运行（`dabench run-benchmark` 配合 `max_workers > 1`）时，往往会遭遇**随机超时**和**任务被强杀**的情况（例如：`Task timed out after 600 seconds. Process killed.`），其根本原因在于：

1. **DuckDB 扩展文件锁冲突**：DuckDB 在多进程并发加载 `.duckdb_extensions` 扩展时，会竞争同一个文件锁。如果一个耗时较长的任务在持有锁期间被主进程强行杀死，该文件锁将成为孤儿锁，导致后续所有并发的新任务在启动加载时永久挂起并超时。
2. **环境依赖与编码问题**：在 Windows 系统上，默认的 `GBK` 编码可能导致写入包含特殊字符的 `prediction.csv` 或 `trace.json` 时崩溃；此外 `uv` 的环境变量配置有时也容易配漏。

通过 **Docker 隔离 + 专用配置 (`max_workers=1`)** 的组合，我们能确保每一个 Task 都在一个绝对安全、无污染的独立环境（或队列）中运行，从而获得与官方评测服务器完全对齐的**100% 测试稳定性**。

---

## 核心改动说明

1. **`Dockerfile`**:
   - 预装了 Python 3.11 和 `uv`。
   - 在构建镜像（Build 阶段）预先离线下载并缓存了 DuckDB 的 `json`、`sqlite` 和 `excel` 扩展，避免在无网沙箱中引发超时。
2. **`docker-compose.yml`**:
   - 实现了项目目录挂载，确保你在宿主机可以直接查阅跑分输出的 `prediction.csv` 与 `trace.json`。
   - `data/public/input` 设置为只读挂载 (`:ro`)，杜绝 Agent 误删评测数据。
3. **`duckdb_exec.py` 优化**:
   - 增加了 `SET autoinstall_known_extensions=false;` 和 `SET autoupdate=false;`。这阻止了 DuckDB 在运行时去网络检查更新和抢夺全局写入锁。
4. **`configs/react_baseline.docker.yaml`**:
   - 提供了一个开箱即用的 Docker 专用配置：强制 `max_workers: 1` 和 `task_timeout_seconds: 600`，确保任务稳健排队执行。

---

## 如何操作？

### 步骤 1：准备数据与环境变量
确保你已经下载了题目数据解压到 `data/public/input` 目录下。

### 步骤 2：启动全量评测
在项目根目录，使用 Docker Compose 一键构建并启动跑分（请将 `your_api_key_here` 替换为你的真实 API Key）：

```bash
# Mac / Linux:
DATA_AGENT_API_KEY="sk-xxxx" docker-compose up --build

# Windows (PowerShell):
$env:DATA_AGENT_API_KEY="sk-xxxx"
docker-compose up --build
```

容器启动后，它会自动执行 `run-benchmark`，并在内部安全、串行地跑完 50 道题。由于避免了进程打架，虽然整体耗时可能在一到两个小时，但你可以放心去喝咖啡，**不会再发生莫名其妙的 Timeout Killed。**

### 步骤 3：查看结果
跑分结束后，容器会自动停止。你可以直接在宿主机的 `artifacts/runs/docker_benchmark_run/` 目录下查看最终的成绩单 `summary.json` 以及每一个 Task 的 `trace.json` 和 `prediction.csv`。

> **💡 小贴士：**
> 如果你需要临时测试单道题，可以直接修改 `docker-compose.yml` 中的 `command` 为：
> `command: ["run-task", "task_75", "--config", "configs/react_baseline.docker.yaml"]`
