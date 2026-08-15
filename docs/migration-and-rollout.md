# Migration and Rollout Plan

> 本文档对应 `runtime-hardening-multiagent-collab` 变更集。  
> 变更分 4 个阶段独立可发布，每阶段含回滚路径。

---

## 架构变更概览

| 阶段 | 内容 | 行为影响 |
|------|------|----------|
| 1 | HITL 审批闭环 + 中间件公开化 | **BREAKING**：审批从占位改为强制闭环；私有中间件依赖移除 |
| 2 | 多智能体协作（全字段 + 动态发现 + 多向委派） | SubAgent 描述格式变更；新建 Agent 自动触发图失效 |
| 3 | 流式 SSE + 任务规划 + 沙箱真实化 | **BREAKING**：沙箱不可用时明确报错，不静默回退宿主执行 |
| 4 | 发布验证 + 迁移文档 | 无运行时影响 |

---

## Breaking Changes

### 1. HITL 审批强制闭环（Phase 1）

**之前**：配置 `interrupt_on` 后 Agent 中断，但无审批决策回写路径，会话挂起。  
**之后**：中断后必须通过 `POST /api/chat/{agent_id}/sessions/{session_id}/approval` 提交决策（approve/edit/reject），否则会话保持等待态。

**迁移动作**：
- 前端必须接入审批卡组件（已提供 `ApprovalCard`）。
- 已有依赖"中断后自动超时恢复"的调用方需改为显式审批。

### 2. 沙箱不再静默回退（Phase 3）

**之前**：配置 `backend.type: sandbox` 但沙箱不可用时，静默回退到本地宿主执行。  
**之后**：沙箱不可用时抛出 `SandboxUnavailableError`，不静默回退。

**迁移动作**：
- 未配置沙箱的 Agent 不受影响（仍使用本地 workspace 后端）。
- 配置了沙箱的 Agent 需确保 E2B/Daytona 后端可用，否则 Agent 创建失败。
- 调用方需捕获 `SandboxUnavailableError` 并给出用户提示。

### 3. 中间件公开化（Phase 1）

**之前**：`_ToolExclusionMiddleware` 依赖 SDK 私有 `_tool_exclusion` 内部实现。  
**之后**：工具排除通过公开中间件 API 实现，不再依赖 SDK 内部类。

**迁移动作**：
- 无外部 API 变更，对调用方透明。
- SDK 升级时不再存在静默失效风险。

### 4. SubAgent 描述格式变更（Phase 2）

**之前**：委派描述为 `f"Agent: {agent_id}"` 模板。  
**之后**：描述从 workspace `description` 字段 + 能力摘要动态生成，格式为 `Agent '{agent_id}' — {description} | Tools: ... | HITL: ...`。

**迁移动作**：
- 依赖旧描述格式的前端展示或日志解析需适配。
- 建议在 workspace `agent.json` 的 `settings.description` 中填写有意义的能力描述。

---

## 分阶段发布流程

### Phase 1 — 运行时安全

1. 确认前端审批卡组件已部署。
2. 发布包含 HITL 闭环 + 中间件公开化的版本。
3. 回归测试：配置 `interrupt_rules` → 触发工具调用 → 审批 → 验证审计记录。
4. 监控审批事件量与审计日志完整性。

**回滚**：回退到上一版本，`agent.json` 配置不变。

### Phase 2 — 多智能体协作

1. 确认所有需委派的 Agent 已在 `agent.json` 中设置 `settings.enable_subagents: true`。
2. 发布 SubAgent 全字段 + 动态发现版本。
3. 验证：新建 Agent → 主 Agent 自动发现 → 委派执行 → 上下文隔离。

**回滚**：回退版本后，SubAgent 退回仅透传 name/description/system_prompt 的行为。

### Phase 3 — 能力增强

1. 确认沙箱后端（E2B/Daytona）可用性或关闭沙箱配置。
2. 发布流式 endpoint + 规划 + 沙箱版本。
3. 验证：`POST /api/chat/stream` SSE 四路投影正常。
4. 验证：`enable_planning: true` 的 Agent 暴露 `write_todos` 工具。

**回滚**：
- 流式 endpoint 不可用不影响同步 `POST /api/chat` 端点。
- 沙箱回退需同时回退 `agent.json` 中的 `backend` 配置。

### Phase 4 — 验证

1. 执行全量测试套件（`pytest tests/`）。
2. 完成发布/回滚演练（已在 `test_phase4_publish_rollback.py` 中覆盖）。
3. 确认本文档已更新。

---

## 配置版本化与回滚

Agent 配置以 workspace 目录下的 `agent.json` 为唯一事实来源。  
"版本化"通过以下方式实现：

1. **变更前备份**：修改 `agent.json` 前，复制当前文件为 `agent.json.bak.{timestamp}`。
2. **应用变更**：直接编辑 `agent.json`，变更在下次图重建时生效。
3. **图失效**：通过以下任一方式触发：
   - `POST /api/agents/{agent_id}/reload` — 热重载 workspace + 失效图缓存。
   - 编辑 kernel 文件 / 工具配置后自动失效。
4. **回滚**：将备份文件覆盖 `agent.json`，然后调用 reload API。

```bash
# 示例：回滚 agent.json 到备份版本
cp .agentcore/workspace/agent/default/agent.json.bak.20250101 \
   .agentcore/workspace/agent/default/agent.json
curl -X POST http://localhost:8000/api/agents/default/reload
```

---

## Deprecation Checklist

- [x] HITL 审批从占位升级为强制闭环，前端审批卡已部署。
- [x] 沙箱不可用时明确报错，不再静默回退宿主执行。
- [x] 私有中间件依赖已移除，工具排除通过公开 API 实现。
- [x] SubAgent 描述从模板升级为动态生成。
- [x] 所有生产 Agent 的 `agent.json` 已版本化管理。
- [x] 审批审计事件（`audit.jsonl`）可查询、不可变。
- [x] 发布/回滚演练已完成（`test_phase4_publish_rollback.py`）。
- [ ] 旧目录结构 `.agentcore/workspaces/` 迁移完成后可删除 `migrate_workspace_layout()` 函数（当前保留以兼容存量部署）。
- [ ] `profile_id` 遗留字段引用可在下一大版本清理（当前 `from_state_dict` 已静默忽略）。

---

## 清理计划

| 遗留项 | 位置 | 清理时机 |
|--------|------|----------|
| `migrate_workspace_layout()` | `multi_agent_manager.py` | 确认所有存量部署已完成迁移后移除 |
| `profile_id` 遗留字段 | `agent_runtime.py` L105-106 | 下一大版本移除 `from_state_dict` 中的兼容注释 |
| `AgentService` 旧 Profile 注释 | `service.py` L1-7 | 可在下次文档刷新时清理 |
| `repository.py` 旧 Profile 注释 | `repository.py` L1-7 | 可在下次文档刷新时清理 |
