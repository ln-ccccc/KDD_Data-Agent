# Subagent 模式实施检查清单

- [ ] **Prompt 设计**
  - [ ] 主代理 Prompt 中包含 `delegate_to_data_analyst` 的使用指南。
  - [ ] 创建 `DATA_ANALYST_SYSTEM_PROMPT`，明确子代理的角色和退出条件。

- [ ] **工具实现与注册**
  - [ ] 实现 `_delegate_to_data_analyst` 函数。
  - [ ] 定义工具的 `input_schema`（包含 `sub_task_description` 等）。
  - [ ] 在 `tools/registry.py` 中注册 `delegate_to_data_analyst`。
  - [ ] (可选) 为子代理注册专门的 `subagent_finish` 工具以返回结构化结果。

- [ ] **状态隔离验证**
  - [ ] 确保在 `_delegate_to_data_analyst` 内部创建了全新的 `Agent` 实例。
  - [ ] 确保子代理的 `RuntimeState` 独立于主代理，没有共享内存引用。
  - [ ] 捕获子代理运行过程中的异常，转化为正常的 `ToolExecutionResult(ok=False)` 返回给主代理。

- [ ] **流程与异常处理**
  - [ ] 处理子代理达到 `max_steps` 但未给出明确答案的情况，向主代理返回适当的提示信息。
  - [ ] 验证子代理消耗的 LLM 时间和工具时间是否正确累加或记录到主代理的 Meta 数据中（可选，用于统计）。

- [ ] **代码质量与测试**
  - [ ] 编写对应的单元测试文件。
  - [ ] 运行基准测试确认引入子代理后性能没有退化，且能解决原先主代理难以处理的复杂任务。
