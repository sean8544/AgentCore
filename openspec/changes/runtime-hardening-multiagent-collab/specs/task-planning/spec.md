## Purpose

定义 AgentCore 结构化任务规划的行为契约。系统通过启用 SDK 内置的待办规划能力，使 Agent 暴露待办列表工具，将复杂多步任务分解为可跟踪、可逐步完成的待办项。

## ADDED Requirements

### Requirement: 待办规划能力接入
系统 MUST 通过启用 SDK 内置的待办规划中间件，使已启用的 Agent 获得结构化待办规划能力，能够将复杂任务分解为可跟踪的待办列表。

#### Scenario: 复杂任务被分解
- **WHEN** Agent 面对涉及多个步骤的复杂任务，且其 profile 已启用规划能力
- **THEN** Agent SHALL 能够创建结构化的待办列表并标记各步骤状态。

#### Scenario: 待办状态跟踪
- **WHEN** Agent 正在执行一个已规划的待办列表
- **THEN** 各待办项 SHALL 具有明确的进行中/待办/完成状态，并随执行推进更新。

#### Scenario: 规划能力按需启用
- **WHEN** 某 Agent 未启用规划能力
- **THEN** 该 Agent SHALL 不暴露待办工具，简单问答任务直接响应而不强制规划。

### Requirement: 规划状态持久化
系统 MUST 使待办规划在同一会话线程的多轮对话间保持。

#### Scenario: 跨轮对话保持待办
- **WHEN** Agent 在某一轮创建了待办列表并在后续轮继续该会话
- **THEN** 该待办列表及其状态 SHALL 在会话内保持，供后续轮次读取与更新。
