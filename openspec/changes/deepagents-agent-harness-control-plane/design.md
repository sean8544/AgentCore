## Context

业务动机见 proposal.md。本设计聚焦如何构建以 deepagents 为核心的管理平面，使系统能够从多个配置域安全组合运行时行为，并发布具备确定性的执行 profile。

目标运行时包含若干必须作为一等对象治理的 deepagents 能力：agent 内核文件（agent.md、profile.md、soul.md）、内置 harness 工具、文件系统权限规则、通过 task 的子代理委派、MCP 工具集成，以及 human-in-the-loop 中断机制。

UI 信息架构采用菜单驱动模式，应遵循你提供的参考菜单所体现的操作域：会话入口、控制操作、工作区资产、平台设置。

## Goals / Non-Goals

**Goals:**
- 提供统一且权威的 profile 模型，组合模型、工具、agent 内核定义、策略与委派行为。
- 确保有效运行时 profile 具备确定性、可检查性和版本化能力。
- 提供内置 harness 工具治理，并覆盖后端能力差异导致的可用性变化。
- 执行声明式文件系统规则与人工审批策略，并满足可审计要求。
- 在不修改业务应用代码的前提下支持发布与回滚。

**Non-Goals:**
- 构建脱离 deepagents 的自定义 LLM 编排引擎。
- 定义特定模型厂商绑定的提示词工程方案。
- 以并行执行引擎替代 deepagents 运行时中间件栈。

## Decisions

### Decision 1: 采用 Profile 编译边界
- Choice: 引入编译步骤，将可编辑草稿配置转换为不可变的有效运行时 profile。
- Rationale: 避免运行时歧义，支持版本差异比较，并可稳定回滚。
- Alternatives considered:
  - 每次请求动态解析运行时配置：拒绝，原因是确定性不足且审计困难。
  - 每个 agent 使用单一静态大文件：拒绝，原因是分层与环境覆盖易产生错误。

### Decision 2: 将 Agent 内核建模为分层三文件
- Choice: 使用分层的 agent.md、profile.md、soul.md 表达 agent 身份与行为定义，并附带明确优先级和来源元数据。
- Rationale: 与目标产品模型一致，这三份文件定义 agent 内核，同时保留覆盖行为的可解释性。
- Alternatives considered:
  - 扁平化为单一可编辑提示词文本：拒绝，原因是来源与冲突解析信息丢失。
  - 仅保存运行时解析后的内核结果，不保留来源层：拒绝，原因是运维无法判断意图和归属。

### Decision 3: 区分工具可见策略与能力可用性
- Choice: 有效工具面由两部分共同计算：策略意图与后端能力探测结果。
- Rationale: 避免在后端或版本不支持 execute、delete 时出现无效配置。
- Alternatives considered:
  - 仅信任管理员选择的工具集合：拒绝，原因是问题会在运行时晚期暴露且不透明。

### Decision 4: 强制执行有序文件系统权限语义
- Choice: 实现 first-match-wins 评估规则，并提供策略模拟接口。
- Rationale: 与 deepagents 权限语义一致，并便于运维验证规则行为。
- Alternatives considered:
  - 最具体规则优先语义：拒绝，原因是与目标运行时行为不一致。

### Decision 5: 集中化 HITL 策略与审批审计
- Choice: 将中断策略建模为 profile 配置，并把每次审批决策持久化为不可变审计事件。
- Rationale: 使安全治理可按 profile 配置，并满足合规复核。
- Alternatives considered:
  - 仅在 UI 现场临时审批：拒绝，原因是策略漂移不可复现。

### Decision 6: 采用按版本发布与可逆部署
- Choice: 运行时执行绑定 profile 版本 id；回滚通过版本重新指派完成。
- Rationale: 支持受控发布、事故回滚与轨迹复现。
- Alternatives considered:
  - 原地可变更新 profile：拒绝，原因是历史运行将无法验证。

### Decision 7: 在 UI 中采用菜单对齐的边界上下文
- Choice: 将产品模块直接映射到顶层导航域，并显式定义跨域动作。
- Rationale: 降低运维认知负担，并与职责分工对齐。
- Alternatives considered:
  - 单页统一大表单配置：拒绝，原因是基于角色的工作流会耦合混乱且易出错。

### Decision 8: 能力路径与展示名分离
- Choice: 能力路径使用稳定英文标识，新增独立的中文展示名映射层用于 UI 与文档输出。
- Rationale: 保证 API、存储键、外部集成不受语言切换影响，同时满足中文管理体验。
- Alternatives considered:
  - 直接将能力路径改为中文：拒绝，原因是会破坏兼容性与自动化脚本稳定性。

## Risks / Trade-offs

- [配置复杂度增长] -> Mitigation: 强制 schema 校验、提供默认模板、暴露有效配置差异视图。
- [内核分层策略冲突] -> Mitigation: 明确优先级模型并在编译阶段输出冲突诊断。
- [后端能力漂移] -> Mitigation: 发布阶段执行能力探测，对不支持工具设置告警/阻断门。
- [生产审批瓶颈] -> Mitigation: 按环境与风险等级提供策略模板，并监控可量化 SLA 指标。
- [审计数据量与存储成本] -> Mitigation: 采用结构化留存分层与冷存储导出机制。
- [标识与展示名不一致造成认知偏差] -> Mitigation: 在 UI 中并列展示“中文名 + 英文路径”，并提供复制路径能力。

## Migration Plan

1. 在保持现有运行行为不变的前提下，引入 profile schema 与编译器的草稿模式。
2. 增加 UI 编辑能力，覆盖模型、agent.md/profile.md/soul.md、内置工具、文件系统权限与 HITL 策略。
3. 启用发布流程，生成不可变版本，并为运行会话绑定版本 id。
4. 对新发布 profile 启用执行门禁（工具面、权限规则、HITL）。
5. 上线回滚控制与基于 runbook 的故障恢复流程。
6. 在完成一致性验证后，废弃历史临时配置路径。

## Open Questions

- MCP 凭据默认应归属哪个租户边界（组织/项目/工作区）？
- 面向目标合规要求，最小审计留存窗口应设为多久？
- v1 是否需要支持按权重流量分配，还是仅支持基于环境的晋级发布？