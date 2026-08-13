## 为什么

采用 deepagents 的团队需要一个 Web 管理平面，用于管理 agent 行为、安全策略和运行时组合能力，避免手工维护分散配置文件。当前阶段必须尽快建设该能力，因为生产落地需要对 agent 内核定义、模型与工具配置、人类审批链路提供可审计治理。

## 变更内容

- 引入以 deepagents 运行时 profile 为核心的 agent harness 管理平面。
- 增加 profile 组合能力，覆盖模型设置、MCP 绑定、skills、agent 内核文件（agent.md、profile.md、soul.md）以及子代理与 task 委派策略。
- 增加内置 harness 工具治理，覆盖 ls、read_file、write_file、edit_file、delete、glob、grep、execute、task 的可见性、限制策略与环境可用性。
- 增加声明式文件系统权限管理，支持有序规则评估与读写路径模拟。
- 增加 human-in-the-loop 策略管理，基于工具动作中断规则，并保留审批审计轨迹。
- 增加 profile 版本化、发布回滚流程，以及有效配置的运行时可追踪能力。
- 增加菜单驱动的信息架构，按目标管理平面的导航域组织（聊天/收件箱、控制、工作区、设置）。
- 增加能力命名映射规则：能力路径保持英文稳定标识，UI 与文档支持中文展示名映射。

## 能力范围

### 新增能力
- agent-harness-control-plane（中文展示名：智能体管理平面）：定义基于 deepagents 的 Web 管理平面需求，覆盖 profile 组合、agent 内核注入、HITL 控制、文件系统治理、内置工具治理与菜单化信息架构。

### 修改能力
- 无。

## 影响

- 影响系统：管理平面后端、管理端 Web UI、运行时 profile 编译器、策略评估器、审计与事件流水线。
- 影响 API：新增 profile、策略、发布回滚、运行时检查相关管理接口。
- 依赖关系：依赖 deepagents 在文件系统权限、内置工具、MCP 集成、human-in-the-loop 中断机制，以及管理平面对 agent 内核文件（agent.md、profile.md、soul.md）的集成能力。
- 运行影响：引入跨环境的 agent 配置治理与发布流程。
