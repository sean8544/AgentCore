## Purpose

定义 AgentCore 流式输出与运行时投影的行为契约。系统通过接入 SDK 的流式能力（`astream` 及流式投影），提供消息、子代理、中断与 Token 消耗的实时投影，改善对话与委派过程的实时反馈。

## ADDED Requirements

### Requirement: 流式对话输出
系统 MUST 通过接入 SDK 流式能力提供流式对话 endpoint，将 Agent 的推理与工具执行过程以流式方式实时推送给客户端。

#### Scenario: 消息流式推送
- **WHEN** 客户端发起一次流式对话请求
- **THEN** 系统 SHALL 以流式事件流持续推送增量消息，而非在全部完成后一次性返回。

### Requirement: 运行时投影
系统 MUST 在流式过程中接入 SDK 的子代理、中断与 Token 消耗投影，将其暴露给客户端。

#### Scenario: 子代理调用可见
- **WHEN** 主 Agent 在流式会话中委派子代理
- **THEN** 系统 SHALL 以只读投影暴露该子代理调用及其状态，供前端可视化委派链。

#### Scenario: 中断请求可见
- **WHEN** 流式会话中触发 HITL 中断
- **THEN** 系统 SHALL 在流中呈现中断请求，使前端能够及时展示审批卡。

#### Scenario: Token 消耗可见
- **WHEN** 流式会话推进
- **THEN** 系统 SHALL 在流中提供该会话的 Token 消耗投影。
