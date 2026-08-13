## 1. 阶段 1 — 运行时安全（HITL 闭环 + 中间件公开化）

- [ ] 1.1 新增审批决策 API：`POST /api/chat/{agent_id}/sessions/{session_id}/approval`，入参 decision(approve/edit/reject) 与可选 edited_args，返回审批受理结果。
- [ ] 1.2 在 Chat 响应处理中检测 `Command.interrupt` 事件：发生中断时将会话置为"待审批"暂停态，而非结束或挂死。
- [ ] 1.3 实现审批决策回写：approve/edit 通过 checkpointer 线程 update_state 以原参/编辑参恢复继续工具调用；reject 中断该工具并返回拒绝信息给 Agent。
- [ ] 1.4 将审批决策（操作者、时间戳、decision、最终入参）写入不可变审计事件，复用既有审计流水线。
- [ ] 1.5 新增前端审批卡组件：展示待审批工具调用（操作类型、参数、原因），提供批准/编辑后批准/拒绝三操作并回调审批 API。
- [ ] 1.6 为审批等待增加明确的状态呈现（等待审批中），保证不静默超时、不丢审批请求。
- [ ] 1.7 用 SDK 公开中间件/公开参数替换 `_ToolExclusionMiddleware` 私有 import（[agent_factory.py L457](file:///d:/code/AgentCore/src/agentcore/runtime/agent_factory.py#L457)），移除私有依赖。
- [ ] 1.8 验证工具排除在移除私有依赖后行为一致，且 SDK 升级不再依赖内部实现。
- [ ] 1.9 为 HITL 审批闭环增加场景测试：approve / edit / reject 三路径 + 中断后恢复正确性。
- [ ] 1.10 为工具排除公开化增加回归测试，确保无静默失效。

## 2. 阶段 2 — 多智能体协作（全字段 + 动态发现 + 多向委派）

- [ ] 2.1 扩展 `SubAgentRegistry.get_subagents()`：从 workspace `read_agent_config()` + kernel 读取并完整映射 SubAgent 的 tools/model/permissions/interrupt_on/skills 字段。
- [ ] 2.2 从 workspace description 字段 + 能力摘要动态生成委派 description，替代 `f"Agent: {agent_id}"` 模板。
- [ ] 2.3 在 `create_agent`/`reload` 路径对"所有 `enable_subagents=true` 的 Agent"触发图失效，实现动态发现（复用 [chat_router.py L139-180](file:///d:/code/AgentCore/src/agentcore/runtime/chat_router.py#L139-L180)）。
- [ ] 2.4 将委派能力从"仅 default 硬编码"改为由 `enable_subagents` 开关决定，支持任意 Agent 作为主 Agent 委派。
- [ ] 2.5 保持防自委派：图构建时始终排除当前主 Agent 自身。
- [ ] 2.6 保持委派上下文隔离：子代理独立上下文，仅回传最终结果。
- [ ] 2.7 为 SubAgent 专精配置增加场景测试：工具集/权限/模型独立生效。
- [ ] 2.8 为动态发现增加测试：新建子 Agent 后主 Agent 图重建并纳入委派列表。

## 3. 阶段 3 — 能力增强（流式 + 规划 + 沙箱）

- [ ] 3.1 接入 SDK `astream` 流式能力，新增流式对话模式，以 SSE 输出。
- [ ] 3.2 暴露 messages / subagents / interrupts / token_usage 四路流式投影。
- [ ] 3.3 在前端接入流式，使对话与委派过程实时可见。
- [ ] 3.4 接入 SDK 内置 `TodoListMiddleware`：在 `AgentFactory` 中间件列表按 profile 开关追加（默认关、per-agent opt-in），启用后暴露 SDK 的 `write_todos` 工具。
- [ ] 3.5 验证未启用规划的 Agent 不暴露待办工具，简单问答直接响应。
- [ ] 3.6 将沙箱后端抽象为接口，接入 E2B（或 Daytona）真实隔离运行时。
- [ ] 3.7 沙箱不可用时明确报错，不静默回退宿主执行（与 spec 对齐）。
- [ ] 3.8 为流式四路投影增加测试（子代理可见、中断可见、token 可见）。

## 4. 发布与验证

- [ ] 4.1 对照 specs/ 下全部新增与修改 requirement 逐条执行验证（含 `agent-harness-control-plane` 的 MODIFIED requirement）。
- [ ] 4.2 增加集成测试覆盖跨阶段联动（HITL 审批 + 子代理 + 流式中断投影）。
- [ ] 4.3 执行分阶段发布与回滚演练，确认 profile 版本化在运行时真实应用（含回滚后重新指向历史版本）。
- [ ] 4.4 更新迁移/废弃说明，清理历史临时配置。
