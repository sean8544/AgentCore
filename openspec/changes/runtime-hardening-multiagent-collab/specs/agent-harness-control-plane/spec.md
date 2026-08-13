## MODIFIED Requirements

### Requirement: Human-in-the-Loop 策略管理
管理平面 MUST 能够配置并执行针对敏感工具调用的 human-in-the-loop 中断策略，且该策略在运行时 SHALL 真实生效（完整审批闭环），而非仅配置层就绪。

#### Scenario: 工具中断策略生效
- **WHEN** 某个 profile 为工具动作定义了中断规则
- **THEN** 运行时执行 SHALL 在工具执行前暂停，并要求审批决策。

#### Scenario: 审批动作可审计
- **WHEN** 审批人对暂停的工具调用执行通过、拒绝或参数修改
- **THEN** 系统 SHALL 在不可变审计记录中保存操作者、时间戳、决策类型和最终工具入参。

#### Scenario: 审批决策驱动执行继续
- **WHEN** 审批人对暂停的工具调用做出批准、编辑后批准或拒绝决策
- **THEN** Agent SHALL 依据该决策继续执行或终止该工具调用，而非挂起或静默超时。

#### Scenario: 审批等待不静默失效
- **WHEN** 工具调用因审批策略而暂停
- **THEN** 系统 SHALL 明确呈现等待状态并保留审批请求，直至收到决策。

### Requirement: 发布回滚与运行时可追踪
管理平面 MUST 支持 agent 运行时 profile 的版本化发布与回滚流程，并将每次运行关联到唯一有效 profile 版本，且该版本化配置 SHALL 在运行时真实应用并可追溯。

#### Scenario: 发布生成不可变版本
- **WHEN** 执行 profile 发布
- **THEN** 系统 SHALL 生成可寻址、不可变的版本工件。

#### Scenario: 运行轨迹可关联版本
- **WHEN** 查看一次执行轨迹
- **THEN** 轨迹 SHALL 包含精确 profile 版本标识，以及该次运行使用的有效工具与策略摘要。

#### Scenario: 回滚后运行时重新指向历史版本
- **WHEN** 执行 profile 回滚
- **THEN** 后续 Agent 运行时 SHALL 使用被回滚指向的历史版本配置，且新运行轨迹记录该版本标识。
