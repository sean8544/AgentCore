## Purpose

定义 AgentCore 多智能体协作的运行时行为契约，覆盖子代理配置映射、委派描述生成、动态发现与多向委派，使主 Agent 能以专精、隔离、可审计的方式编排其他 Agent。

## ADDED Requirements

### Requirement: 子代理专精配置映射
系统 MUST 在将已加载 Agent 注入为主 Agent 的子代理时，透传完整的专精配置（工具集、模型、权限、HITL 中断策略、技能），而非仅透传名称、描述与系统提示。

#### Scenario: 子代理保留专精工具集
- **WHEN** 一个 Agent 被配置为其他主 Agent 的可委派子代理，且其 profile 定义了独立工具集
- **THEN** 该子代理在委派执行时 SHALL 使用其自身配置的工具集，而非继承主 Agent 的全部工具。

#### Scenario: 子代理权限收窄
- **WHEN** 主 Agent 委派任务给一个配置了受限文件系统权限的子代理
- **THEN** 该子代理 SHALL 仅能在其权限范围内操作，越权访问被拒绝。

#### Scenario: 子代理独立模型选择
- **WHEN** 子代理 profile 显式指定了模型
- **THEN** 该子代理 SHALL 使用指定模型执行，而非主 Agent 的默认模型。

### Requirement: 委派描述动态生成
系统 MUST 为每个可委派子代理生成面向行动、可区分能力的委派描述，供主 Agent 的 LLM 据此决定是否及何时委派。

#### Scenario: 描述反映子代理能力
- **WHEN** 系统为某子代理生成委派描述
- **THEN** 描述 SHALL 反映其职责与能力边界，而非仅包含 Agent 标识符。

#### Scenario: 描述随能力变更更新
- **WHEN** 子代理的能力配置（职责、技能、工具）发生变化
- **THEN** 系统 SHALL 重新生成其委派描述，使主 Agent 在下次图构建时使用最新描述。

### Requirement: 子代理动态发现
系统 MUST 使主 Agent 在新建或变更可委派子代理后，能够发现并将其纳入委派范围，无需人工重启。

#### Scenario: 新建子代理被主 Agent 发现
- **WHEN** 用户新建一个可被委派的 Agent
- **THEN** 所有开启了委派开关的主 Agent 在下次会话构建时 SHALL 将该新 Agent 纳入委派列表。

#### Scenario: 防自委派保持
- **WHEN** 为某主 Agent 构建委派列表
- **THEN** 系统 SHALL 始终排除该主 Agent 自身，避免无限递归委派。

### Requirement: 多向委派
系统 MUST 支持任意开启委派开关的 Agent 作为主 Agent 委派其他 Agent，而非仅限于特定内置 Agent。

#### Scenario: 任意 Agent 可委派
- **WHEN** 用户对任意一个开启了委派开关的 Agent 发起对话
- **THEN** 该 Agent SHALL 具备委派能力，可调用其他已加载的可委派子代理。

#### Scenario: 委派上下文隔离
- **WHEN** 主 Agent 委派任务给子代理
- **THEN** 子代理的执行上下文 SHALL 与主 Agent 隔离，仅将最终结果回传主 Agent，中间过程不进入主上下文。
