## Context

现状（见 proposal.md - Why）：
- 控制平面骨架已建成：profile 组合、权限规则、HITL `interrupt_on` 配置映射（[agent_factory.py L436-440](file:///d:/code/AgentCore/src/agentcore/runtime/agent_factory.py#L436-L440) 已把 `interrupt_rules` 转成 `interrupt_on` 传入 `create_deep_agent`）、发布回滚、审计事件。
- `astream` 流式能力已存在于 [agent_runtime.py L405](file:///d:/code/AgentCore/src/agentcore/runtime/agent_runtime.py#L405)，但未暴露为对外流式 endpoint。
- SubAgent 仅透传 name/description/system_prompt（[subagent_registry.py L71-76](file:///d:/code/AgentCore/src/agentcore/runtime/subagent_registry.py#L71-L76)）；图缓存快照导致新建 Agent 不自动发现（[chat_router.py L213-216](file:///d:/code/AgentCore/src/agentcore/runtime/chat_router.py#L213-L216)）；工具排除依赖 SDK 私有 `_ToolExclusionMiddleware`（[agent_factory.py L457-459](file:///d:/code/AgentCore/src/agentcore/runtime/agent_factory.py#L457-L459)）。

约束：仅用 SDK 公开 API；HITL 依赖 SDK interrupt 机制 + checkpointer；图是编译期注入，无运行时增删 subagent API。

## Goals / Non-Goals

**Goals:**
- 打通 HITL 审批运行时闭环（触发→呈现→决策→回写→继续）。
- 让 SubAgent 携带完整专精配置，并实现动态发现与多向委派。
- 以 SDK 公开中间件 API 替换私有依赖，并开放全局中间件入口。
- 暴露流式 endpoint（四路投影，接入 SDK `astream`）；启用 SDK 内置任务规划；将沙箱从占位提升为真实隔离执行。

**Non-Goals:**
- 多主对等协作网络（SDK 不支持，本变更不做）。
- 子 Agent 嵌套委派（SDK 语义不支持）。
- 模型训练/微调、完整 BI 报表。
- 生产级沙箱的极限加固（E2B 层职责）。

## Decisions

### D1. HITL 审批闭环：中断感知 + 审批决策 API + 继续恢复
- **现状**：`interrupt_on` 已配置，但前端无审批卡、无决策回写路径，Agent 中断后无法恢复（即诊断的"运行时挂起/超时"）。
- **方案**：
  1. Chat 响应改为可感知 `Command.interrupt` 事件（复用 `astream`，检测 `interrupt` 投影/状态），当发生中断时，暂停而非结束会话。
  2. 新增审批决策 API：`POST /api/chat/{agent_id}/sessions/{session_id}/approval`，入参 `decision ∈ {approve, edit, reject}` 与可选的 `edited_args`。
  3. 决策通过 checkpointer 的线程 `update_state` 恢复：approve/edit → 以原参/编辑参继续该工具；reject → 中断该工具并返回拒绝信息给 Agent。
  4. 决策写入不可变审计事件（复用既有审计流水线）。
- **备选**：SSE 审批通道。因 MVP 只需单次决策，用轮询/长请求的审批 API 更简单；SSE 用于 D5 流式，两者解耦。

### D2. SubAgent 专精配置 + 动态发现 + 多向委派：一次链路改造
- **专精配置**：`SubAgentRegistry.get_subagents()` 从每个 workspace 的 `read_agent_config()` + kernel 读取 tools/model/permissions/interrupt_on/skills，完整映射进 `SubAgent` 字段（补齐 `tools`/`model`/`permissions`/`interrupt_on`/`skills`）。
- **动态发现**：`create_agent`/`reload` 后，对"所有 `enable_subagents=true` 的 Agent"调用 `invalidate_agent_graph`，图重建时重新聚合（复用 [chat_router.py L139-180](file:///d:/code/AgentCore/src/agentcore/runtime/chat_router.py#L139-L180) 的失效逻辑）。
- **多向委派**：委派能力由 `enable_subagents` 开关决定（而非 default 硬编码），移除"仅 default 启用"的特殊分支，任何 Agent 开启开关即可委派。
- **description 动态生成**：从 workspace 的 description 字段 + 能力摘要生成，替代模板字符串。

### D3. 中间件：公开 API 替换私有依赖 + 全局扩展口
- **替换私有**：用 SDK §6 公开的 `FilesystemMiddleware`/`HarnessProfile` 或 `create_deep_agent` 公开的 `excluded_tools`/白名单参数替代 `_ToolExclusionMiddleware`，移除私有 import（[agent_factory.py L457](file:///d:/code/AgentCore/src/agentcore/runtime/agent_factory.py#L457)）。
- **全局扩展口**：在存储层新增 `global_middleware` 配置，`AgentFactory.create_agent()` 读取并在第 7 位插入点合并注入；支持按 Agent 选择性应用。
- **备选**：维护私有 import 并 try/except。被否：SDK 升级静默失效风险不可接受。

### D4. 沙箱真实化
- **方案**：将 sandbox 后端抽象为接口，接入 E2B（或 Daytona）真实运行时；`virtual_mode` 仍默认（本地工作区），仅当配置 sandbox 时才路由到远程沙箱。
- **降级语义**：沙箱不可用时明确报错，不静默回退宿主执行（与 spec 的"沙箱不可用明确失败"对齐）。

### D5. 流式 endpoint
- **方案**：接入 SDK 已有的 `astream` 流式能力，新增 `POST /api/chat/stream`（或给现有 chat 加 `stream=true` 模式），基于 [agent_runtime.py L405](file:///d:/code/AgentCore/src/agentcore/runtime/agent_runtime.py#L405)，以 SSE 输出 `messages` / `subagents` / `interrupts` / `token_usage` 四路投影。

### D6. 任务规划
- **方案**：接入 SDK 内置 `TodoListMiddleware`，在 `AgentFactory` 中间件列表按 profile 开关追加（默认关、per-agent opt-in），启用后 Agent 暴露 SDK 的 `write_todos` 工具，获得结构化待办能力（接入而非自研）。

## Risks / Trade-offs

- [HITL 恢复依赖 checkpointer `update_state` 语义] → 用 SDK 官方中断恢复示例验证，先做最小闭环（approve/reject/edit）再扩展。
- [SubAgent 全字段映射引入配置漂移（工具面不一致）] → 复用既有的发布前工具面校验门禁，对子代理同样校验。
- [动态发现全量失效重建成本] → 只失效 `enable_subagents=true` 的 Agent，而非全局清空。
- [沙箱接入引入外部依赖与网络开销] → 默认关闭，仅显式配置时启用；不可用明确失败。

## Migration Plan

1. **阶段 1（运行时安全）**：HITL 闭环（D1）+ 中间件替换/扩展口（D3）——先修静默失效与信任裂缝。
2. **阶段 2（多智能体协作）**：SubAgent 全字段 + 动态发现 + 多向委派 + description（D2）。
3. **阶段 3（能力增强）**：流式（D5，接入 `astream`）+ 规划（D6，启用 `TodoListMiddleware`）+ 沙箱（D4）。
4. 每阶段独立可发布；HITL 与沙箱属行为变更，需回归测试；回滚依赖 profile 版本化（已具备）。

## Open Questions

- E2B/Daytona 沙箱的账号与额度接入方式（不影响 spec 与任务拆分，实现时选定）。
