# Default Agent 与新建 Agent 通讯机制分析报告

> **生成日期**: 2026-08-13
> **评估基准**: [./deepagents-完全说明书.md](./deepagents-完全说明书.md) §4.8 子代理 + §6 中间件 + §8 安全最佳实践

---

## 一、通讯机制全景

### 1.1 Default Agent 的创建

系统首次启动时，`api.py` 的 `_ensure_default_agent()` 创建一个 `agent_id="default"` 的 Agent。关键在于：这个 default agent 的 `agent.json` 被自动写入 `settings.enable_subagents: true`：

```python
if self.agent_id == "default":
    config["settings"]["enable_subagents"] = True
```

而**新建的 Agent 默认不开启**此设置（`DEFAULT_AGENT_JSON` 中 `settings` 为空 `{}`）。

### 1.2 通讯路径：SDK SubAgent 机制（task 工具）

通讯**完全依赖 deepagents SDK 的 SubAgent 机制**，不是自定义协议、不是 HTTP、不是 A2A。完整调用链如下：

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. 用户向 default agent 发消息                                       │
│    POST /api/chat  { agent_id: "default", message: "..." }          │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. chat_router._get_or_create_agent_graph()                         │
│                                                                     │
│    检查 settings.enable_subagents == true  →  是                    │
│    ↓                                                                │
│    SubAgentRegistry(manager).get_subagents(                         │
│        exclude_agent_id="default"                                   │
│    )                                                                │
│    遍历 MultiAgentManager._workspaces 中所有其他已加载的 agent      │
│    →  返回 list[SubAgent]                                           │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. AgentFactory.create_agent(subagents=[...])                       │
│                                                                     │
│    kwargs["subagents"] = subagents                                  │
│    →  create_deep_agent(subagents=[SubAgent(...), ...])             │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 4. SDK 内部: SubAgentMiddleware (中间件栈第 3 位)                    │
│                                                                     │
│    自动注册 task 工具到 default agent 的工具列表                     │
│    default agent 的 LLM 可以生成 tool_call:                         │
│      task(subagent_name="agent-B", description="做某件事")          │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 5. SDK 内部: SubAgentMiddleware 拦截 task tool_call                 │
│    (SDK §4.8 "子代理调用机制")                                      │
│                                                                     │
│    • 查找 name="agent-B" 的 SubAgent 配置                           │
│    • 用该配置创建独立的 LangGraph agent 实例                        │
│    • 创建隔离的上下文（空消息历史）                                  │
│    • 同步执行子代理图（阻塞，等待完成）                              │
│    • 只有最终文本作为 ToolMessage 返回给 default agent              │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.3 核心特性

| 特性 | 实际行为 | SDK 条款 |
|------|----------|----------|
| **通讯协议** | 纯进程内函数调用，无网络 | §4.8 "通信协议：不是 A2A，是进程内调用" |
| **上下文隔离** | 子代理有独立 messages 列表，中间结果不回传 | §4.8 "隔离的上下文窗口" |
| **返回方式** | 只有最终文本作为 ToolMessage 返回 | §4.8 "只有最终文本返回" |
| **方向性** | **单向**：default → 其他 agent；其他 agent 不能反向委派给 default | — |
| **触发方式** | LLM 自主决策调用 `task` 工具 | §4.8 "LLM 决定委派" |

---

## 二、他们属于 SubAgent 吗？

**是的**。当 default agent 的 `enable_subagents=true` 时，`SubAgentRegistry.get_subagents()` 将所有其他已加载的 agent 包装成 SDK 的 `SubAgent` 对象：

```python
SubAgent(
    name=agent_id,                              # ✅ 传了
    description=f"Agent: {agent_id}",            # ⚠️ 模板字符串
    system_prompt=system_prompt or _EMPTY_PROMPT_FALLBACK,  # ✅ 传了
)
```

这些 `SubAgent` spec 通过 `create_deep_agent(subagents=[...])` 传入 SDK，SDK 的 `SubAgentMiddleware` 自动注册 `task` 工具。从 SDK 视角看，它们就是标准的同步子代理。

---

## 三、符合 deepagents 最佳实践吗？

**不完全符合**。以下逐条对比 SDK §4.8 "子代理最佳实践"的 5 条要求：

---

### ✅ 符合的部分

**1. SubAgent 机制选型正确 — 使用了 SDK 原生 SubAgent + task 工具，而非自建通讯**

项目没有自己实现 Agent 间的 HTTP 调用或消息队列，而是完全依赖 SDK §4.8 的 `SubAgentMiddleware` + `task` 工具。这保证了上下文隔离、进程内调用的低延迟、以及与 SDK 中间件栈（摘要、HITL 等）的天然兼容。

**2. 默认关闭、按需开启，符合 §8 "最小化工具集"安全原则**

```
Note: subagents are **opt-in** (default disabled) so agents are not
mutually visible unless explicitly configured.
```

新建 Agent 默认不开启 `enable_subagents`，只有 default agent 首次创建时自动开启。避免了所有 Agent 互相可见导致的安全风险。

**3. exclude_agent_id 防止自委派**

```python
if agent_id == exclude_agent_id:
    continue
```

正确避免了 Agent 委派给自己的无限递归。

---

### ❌ 不符合的部分

**1. description 用模板字符串 `f"Agent: {agent_id}"`，严重违反 §4.8 最佳实践第 1 条**

SDK §4.8 明确要求：
> `description` — **必填**。面向行动的描述，主 Agent 据此决定何时委派

SDK §4.8 最佳实践第 1 条：
> **写清晰的 description**——主 Agent 据此决定何时委派，描述要具体且面向行动

当前实现：
```python
description=f"Agent: {agent_id}",
```

这个描述**毫无信息量** —— LLM 无法据此判断"这个子代理擅长什么任务、应该在什么场景下委派"。比如有一个名为 `code-reviewer` 的 agent，LLM 看到的 description 是 `"Agent: code-reviewer"`，完全不知道它能做代码审查。正确做法是从子代理的 `agent.md` 或 `description` 字段中提取真实的能力描述。

**2. 只填了 3/10 字段，SDK §4.8 完整字段参考的 7 项缺失**

| 字段 | SDK 用途 | 当前状态 | 影响 |
|------|----------|----------|------|
| `name` | 唯一标识 | ✅ | — |
| `description` | 委派路由依据 | ⚠️ 模板 | LLM 不知道何时委派 |
| `system_prompt` | 子代理人格 | ✅ | — |
| **`tools`** | 最小化工具集 | ❌ 缺失 | 子代理**继承父 Agent 的全部工具**（包括读写删 shell），无法做权限收窄。违反 §4.8 L1296 "指定则完全覆盖" + §4.8 L1423 "最小化工具集" |
| **`model`** | 按任务选模型 | ❌ 缺失 | 所有子代理用同一模型。§4.8 L1424 "简单子代理用便宜模型，复杂推理用强模型"的成本优化完全失效 |
| **`interrupt_on`** | 独立审批策略 | ❌ 缺失 | 子代理继承父 Agent 的 HITL 配置，无法独立设置。§4.8 L1299 "默认继承；指定则覆盖" |
| **`permissions`** | 权限收窄 | ❌ 缺失 | 子代理继承父 Agent 的文件系统权限。§4.8 L1302 "默认继承；指定则完全替换" |
| `middleware` | 独立中间件 | ❌ 缺失 | 无法给子代理加 PII/审计 |
| `skills` | 独立技能集 | ❌ 缺失 | §4.8 L1300 "不继承" |
| `response_format` | 结构化输出 | ❌ 缺失 | 低优先级 |

**3. 通讯是单向的 — 只有 default 能委派，其他 agent 无法反向委派**

由于只有 default agent 默认 `enable_subagents=true`，新建 agent 默认不开启。这意味着：
- `default → agent-B` ✅ 可以委派
- `agent-B → default` ❌ 无法委派
- `agent-B → agent-C` ❌ 无法委派

这不是 SDK 的限制（SDK 完全支持任意 Agent 配置子代理），而是项目的配置策略。如果用户想实现多 Agent 互相协作，必须手动给每个 agent 都开启 `enable_subagents`。

**4. SubAgent 的 system_prompt 来自 kernel 文件但可能为空 — 用 fallback 填充违反 §4.8 要求**

```python
system_prompt = workspace.get_system_prompt()
# ...
system_prompt=system_prompt or _EMPTY_PROMPT_FALLBACK,
```

`_EMPTY_PROMPT_FALLBACK = "You are a helpful AI assistant."`

SDK §4.8 最佳实践第 2 条明确要求：
> **保持 system_prompt 详细**——子代理不继承主 Agent 的系统提示，需包含完整的工具使用和输出格式指导

如果一个新建 agent 还没编辑过 `agent.md`，它的 system_prompt 就会回退为"你是一个有用的 AI 助手"——这个 prompt 既没有工具使用指导，也没有输出格式要求，子代理的行为完全不可控。

**5. SubAgent 列表在图构建时快照，新增 agent 不会实时反映**

`get_subagents()` 确实实时遍历 `_workspaces`，但它的结果只在 **graph 构建时**（即 `_get_or_create_agent_graph()` 第一次缓存时）被调用一次，之后图被缓存在 `graph_cache` 中。这意味着：
- default agent 对话期间新建了一个 agent-C → agent-C **不会**出现在 default 的可委派列表中
- 必须等到 `invalidate_agent_graph()` 清除缓存后重建图，agent-C 才会出现

这不是 SDK 限制，而是项目的缓存策略导致的。不过 `invalidate_agent_graph()` 在修改 kernel 文件时会触发，算是部分缓解。

---

## 四、总结

| 维度 | 评价 |
|------|------|
| **通讯机制选型** | ✅ 正确使用了 SDK 原生 SubAgent + task 工具，进程内调用，无自造协议 |
| **SubAgent 身份** | ✅ 是标准 SDK SubAgent，通过 `create_deep_agent(subagents=[...])` 注入 |
| **description 质量** | ❌ 模板字符串 `f"Agent: {agent_id}"`，LLM 无法据此做委派决策 |
| **字段完整性** | ❌ 3/10，缺 tools/model/interrupt_on/permissions 等 7 项，成本优化和权限收窄完全不可用 |
| **system_prompt 质量** | ⚠️ 来自 kernel 但可能为空，fallback 是无指导的通用 prompt |
| **通讯方向** | ⚠️ 单向（仅 default → 其他），其他 agent 间互相不可委派 |
| **默认安全策略** | ✅ 默认关闭、opt-in、exclude_self，符合 §8 最小化原则 |
| **实时性** | ⚠️ 图缓存导致新增 agent 不能实时出现在委派列表中 |

### 对照 §4.8 五条最佳实践的接通率

| # | SDK §4.8 最佳实践条款 | 接通状态 |
|---|------------------------|----------|
| 1 | 写清晰的 description（面向行动） | ❌ 未接通（模板字符串） |
| 2 | 保持 system_prompt 详细（含工具/格式指导） | ⚠️ 半接通（有 kernel 但 fallback 过于通用） |
| 3 | 最小化工具集（per-subagent） | ❌ 未接通（缺 tools 字段，默认全继承） |
| 4 | 按任务选模型（成本优化） | ❌ 未接通（缺 model 字段，统一模型） |
| 5 | 要求简洁返回（在 system_prompt 中约定） | ⚠️ 取决于 kernel 内容是否约定 |

**接通率：0.5/5，10%**

---

## 五、一句话总结

> **通讯机制的路走对了（SDK 原生 SubAgent + task 工具 + 进程内调用 + 进程内安全默认策略），但 SubAgent spec 的填充严重不足 —— 只传了 3 个字段、description 是无信息模板、单向通讯。对照 SDK §4.8 的 5 条最佳实践，只满足了"默认关闭=最小化工具集"1 条的默认策略层面，其余 4 条（清晰 description、详细 system_prompt、per-subagent 最小工具集、按任务选模型）均未满足。核心修改点：把 SubAgent 字段从 3 项补到 10 项 + description 从 agent.md 的实际内容提取而非模板化。**
