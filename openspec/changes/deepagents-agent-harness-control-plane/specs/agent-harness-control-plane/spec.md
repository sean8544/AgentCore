## Purpose

定义一个以 deepagents 为核心的 Web 管理平面行为契约，用于组合 agent 运行时 profile、治理内置能力，并在跨环境运行中提供可审计的安全保障。

## ADDED Requirements

### Requirement: 组合式 Agent 运行时 Profile
管理平面 MUST 为每个 agent 组合一个可发布的运行时 profile，至少包括模型配置、MCP 绑定、skills、agent 内核文件（agent.md、profile.md、soul.md）、文件系统权限、内置工具策略与委派策略。

#### Scenario: 有效 Profile 具备确定性
- **WHEN** 用户在目标环境中打开某个指定的 profile 版本
- **THEN** 系统 SHALL 返回唯一且可重现的有效配置快照，并完成所有继承与覆盖值解析。

#### Scenario: 有效 Profile 可解释
- **WHEN** 比较两个 profile 版本
- **THEN** 系统 SHALL 提供字段级差异结果，标识新增、变更与删除的运行时设置。

### Requirement: Agent 内核文件治理
管理平面 MUST 支持基于 agent.md、profile.md、soul.md 的分层内核定义，并提供明确优先级与来源归属。

#### Scenario: 内核层优先级被强制执行
- **WHEN** 组织层、项目层、agent 层内核定义存在冲突
- **THEN** 系统 SHALL 按文档化优先级顺序进行解析，并将结果持久化到有效 profile。

#### Scenario: 内核来源可追溯
- **WHEN** 运维人员查看有效 agent 内核内容
- **THEN** 系统 SHALL 展示每个指令块来自哪个层级来源。

#### Scenario: 内核文件可在 UI 中编辑
- **WHEN** 运维人员在管理平面编辑 agent.md、profile.md 或 soul.md
- **THEN** 系统 SHALL 进行结构校验、保存草稿版本，并在编译预览中纳入该变更。

### Requirement: Human-in-the-Loop 策略管理
管理平面 MUST 能够配置并执行针对敏感工具调用的 human-in-the-loop 中断策略。

#### Scenario: 工具中断策略生效
- **WHEN** 某个 profile 为工具动作定义了中断规则
- **THEN** 运行时执行 SHALL 在工具执行前暂停，并要求审批决策。

#### Scenario: 审批动作可审计
- **WHEN** 审批人对暂停的工具调用执行通过、拒绝或参数修改
- **THEN** 系统 SHALL 在不可变审计记录中保存操作者、时间戳、决策类型和最终工具入参。

### Requirement: 文件系统权限规则评估
管理平面 MUST 管理声明式文件系统权限，并对读写操作采用有序 first-match 评估语义。

#### Scenario: 首个命中规则决定结果
- **WHEN** 多条文件系统权限规则都可能命中同一路径
- **THEN** 运行时 SHALL 按声明顺序执行首条命中规则。

#### Scenario: 权限模拟能力可用
- **WHEN** 运维人员输入候选路径和操作进行评估
- **THEN** 系统 SHALL 返回命中的规则及对应的允许或拒绝结论。

### Requirement: 内置 Harness 工具治理
管理平面 MUST 治理内置 harness 工具面，至少包括 ls、read_file、write_file、edit_file、delete、glob、grep、execute、task。

#### Scenario: 环境感知可用性
- **WHEN** 运行时后端不支持某项内置工具能力
- **THEN** 有效 profile SHALL 将该工具标记为不可用，并从暴露工具面中移除。

#### Scenario: Profile 工具策略被执行
- **WHEN** 某个 profile 禁用或限制特定内置工具
- **THEN** 运行会话 SHALL 仅暴露该 profile 与当前环境允许的内置工具集合。

### Requirement: 菜单驱动的信息架构
管理平面 UI MUST 暴露清晰导航域，将会话操作、运行控制、工作区资产与平台设置分离。

#### Scenario: 工作区菜单分组映射功能
- **WHEN** 用户进入工作区导航域
- **THEN** UI SHALL 提供文件、技能、工具、MCP、ACP、运行配置、智能体统计等独立菜单入口。

#### Scenario: 设置菜单分组映射治理功能
- **WHEN** 用户进入设置导航域
- **THEN** UI SHALL 提供智能体管理、模型管理、技能池、环境变量、安全、Token 消耗、备份、语音转写、调试等独立菜单入口。

### Requirement: 能力标识与中文展示名映射
系统 MUST 保持能力标识路径的稳定英文值，并为 UI 与文档提供中文展示名映射能力。

#### Scenario: 稳定标识用于系统集成
- **WHEN** 外部系统通过能力标识读取或关联配置
- **THEN** 系统 SHALL 返回稳定的英文能力路径，不因展示语言变化而变更。

#### Scenario: 中文展示用于管理界面
- **WHEN** 用户在中文界面查看能力列表或详情
- **THEN** 系统 SHALL 显示中文展示名，并可追溯到对应英文能力路径。

### Requirement: 发布回滚与运行时可追踪
管理平面 MUST 支持 agent 运行时 profile 的版本化发布与回滚流程，并将每次运行关联到唯一有效 profile 版本。

#### Scenario: 发布生成不可变版本
- **WHEN** 执行 profile 发布
- **THEN** 系统 SHALL 生成可寻址、不可变的版本工件。

#### Scenario: 运行轨迹可关联版本
- **WHEN** 查看一次执行轨迹
- **THEN** 轨迹 SHALL 包含精确 profile 版本标识，以及该次运行使用的有效工具与策略摘要。
