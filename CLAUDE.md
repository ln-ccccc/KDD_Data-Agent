<!--
本文件用于指导 Claude Code / 其他 coding agent 在本仓库中进行开发与修改。
保持“工程化、可维护、可复现”的风格：小步提交、强约束接口、完善 trace。
-->

# CLAUDE.md

## 项目定位

本仓库是 KDD Cup 2026 DataAgent-Bench 的 starter kit / baseline。

目标：

- 读取 `data/public/input/` 的任务与 context 数据包
- 通过 ReAct（或 hybrid：Plan → ReAct）调用受限工具完成分析
- 写出可评测的 `prediction.csv` 与可调试的 `trace.json`

## 常用命令

```bash
# 安装依赖
uv sync

# 查看项目/数据集状态
uv run dabench status --config configs/react_baseline.example.yaml

# 跑单个任务
uv run dabench run-task task_11 --config configs/react_baseline.example.yaml

# 跑公开 benchmark（可用 --limit 做小规模回归）
uv run dabench run-benchmark --config configs/react_baseline.example.yaml --limit 5
```

## 目录结构（核心关注点）

```
src/data_agent_baseline/
  core/                 # 轻量公共类型与错误定义（尽量无重依赖）
  runtime/              # trace / 运行时辅助（稳定 JSON 输出、元信息）
  benchmark/            # 数据集与 schema
  agents/               # ReAct 与 hybrid (Plan-and-Execute)
  tools/                # 受限工具（文件预览、只读 SQL、Python 执行等）
  run/                  # runner：单任务/批量运行与产物写出
```

## 工程约束与开发风格

1. **保持 CLI 与评测输出兼容**
   - `dabench` CLI、`prediction.csv` 格式、`trace.json` 路径结构不要破坏。

2. **小步改动、优先加“底座抽象”**
   - 新增模块优于大规模移动旧代码；迁移可以逐步做，先保证稳定性。

3. **强约束接口，避免隐式耦合**
   - 工具输出尽量结构化；trace 中记录关键 meta（LLM/tool 耗时、错误信息）。

4. **可靠性优先于花哨**
   - 对模型输出 JSON 解析失败等常见问题，提供明确错误信息与最小重试机制。

5. **变更后更新文档**
   - 改了配置项/模块结构/输出字段，需要同步更新 `README.md` 与 `README.zh.md`。

