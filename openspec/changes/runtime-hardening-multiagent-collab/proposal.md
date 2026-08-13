## Why

AgentCore 的控制平面骨架（profile / 权限 / HITL 策略 / 审计 / 发布回滚）已建成，但对照 deepagents SDK 手册存在两类结构性缺口：**一是"宣称与事实不符"的运行时裂缝**——HITL 审批、SubAgent 专精配置、文件沙箱在配置层已就绪，运行时闭环却未打通或仅为占位，用户配置的能力实际不生效甚至静默失效；**二是产品核心主张"多智能体协作"仍为空壳**——SubAgent 仅透传 3 个字段、委派描述无信息量、新建子 Agent 无法被主 Agent 动态发现、委派方向单向。此外还缺少体验增强所需的流式与规划能力。本变更一次性收敛全部优先级缺口，使产品从"能配置、能跑"走向"配置即生效、多 Agent 真协作"。部分能力（流式、任务规划）为接入/暴露 SDK 已内置能力，而非自研。

## What Changes

- **A. HITL 运行时审批闭环**：打通"触发 interrupt → 前端审批卡 → 决策回写 checkpointer → Agent 继续"的完整链路，使配置的 `interrupt_on` 策略真实生效，而非配置层就绪、运行时挂起。**BREAKING**：审批流从占位改为强制闭环。
- **B. SubAgent 全字段**：`SubAgentRegistry` 由仅透传 name/description/system_prompt 扩展为完整映射 `tools`/`model`/`permissions`/`interrupt_on`/`skills`，使被委派的子 Agent 保持专精配置与权限收窄。
- **C. 委派描述动态生成**：将 `description=f"Agent: {agent_id}"` 模板替换为从 agent 的 description/内核能力动态生成，提升 LLM 委派路由命中率。
- **D. 动态发现**：新建子 Agent 后触发所有 `enable_subagents=true` 主 Agent 图失效重建，使新 Agent 自动进入主 Agent 委派列表。
- **E. 多向协作**：打破"仅 default 可委派"限制，使任意开启委派开关的 Agent 都能委派与接受委派。
- **F. 替换私有中间件**：以 SDK §6 公开 API 替代 `_ToolExclusionMiddleware` 私有 import，消除 SDK 升级静默失效风险。**BREAKING**：移除私有依赖。
- **G. 全局中间件扩展口**：提供跨 Agent 的第 7 位中间件注入机制，支持 PII 脱敏/审计/tracing 热插拔。
- **H. 文件沙箱真实化**：将降级占位的沙箱执行替换为真实隔离执行（E2B/Daytona 或等价后端）。
- **J. 配置版本化/回滚运行时确认**：确认并补齐控制平面已声明的 profile 版本化/回滚在运行时的真实落地与可追溯。
- **K. SSE 流式**：接入 SDK 的 `astream` 与流式投影能力，暴露 `messages/subagents/interrupts/token_usage` 四路投影的流式 endpoint，改善对话与委派实时反馈。
- **L. 任务规划能力**：启用 SDK 内置的 `TodoListMiddleware`，使 Agent 暴露 `write_todos` 工具、获得复杂多步任务的结构化分解能力（接入而非自研）。

## Capabilities

### New Capabilities
- `multiagent-collaboration`: 多智能体协作运行时契约，覆盖 SubAgent 全字段、委派描述动态生成、动态发现、多向委派与内置 general-purpose 委派能力。
- `runtime-security-hardening`: 运行时安全闭环，覆盖 HITL 审批闭环、文件沙箱真实执行与中间件扩展口。
- `middleware-extensibility`: 全局中间件扩展契约，覆盖公开中间件接入点与对私有 SDK 中间件的替换。
- `streaming-observability`: 流式输出与运行时投影契约，覆盖 messages/subagents/interrupts/token_usage 四路投影（接入 SDK `astream`）。
- `task-planning`: 结构化任务规划契约，覆盖启用 SDK `TodoListMiddleware` 暴露 `write_todos` 工具与待办生命周期。

### Modified Capabilities
- `agent-harness-control-plane`: 在既有 HITL 策略管理、内置工具治理、文件系统权限与发布回滚需求之上，将"运行时审批闭环生效"与"profile 版本化运行时可追溯"固化为明确需求（原实现为配置层就绪，需补运行时执行）。

## Impact

- **影响系统**：运行时 `AgentFactory`、`SubAgentRegistry`、`chat_router` 图构建/缓存、`agent_router` 创建链路、中间件栈、流式 endpoint。
- **影响 API**：新增流式 endpoint、审批决策回写、SubAgent 配置映射、全局中间件注册；修改 profile 发布/回滚的运行时应用。
- **依赖关系**：依赖 deepagents SDK §6 公开中间件 API、interrupt/HITL 机制、SubAgent 全字段能力、`TodoListMiddleware`（内置）；沙箱依赖 E2B/Daytona 或等价后端。
- **运行影响**：变更跨运行时安全、协作、治理三块，需分阶段发布；审批与沙箱属行为变更，需回归测试。
