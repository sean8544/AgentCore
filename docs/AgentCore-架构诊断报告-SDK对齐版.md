# AgentCore 管理平面 — 架构诊断报告（基于 SDK 说明书基准）

> **生成日期**: 2026-08-13
> **评估基准**: 仅以 [./deepagents-完全说明书.md](./deepagents-完全说明书.md) 描述的 SDK 架构、最佳实践、§7 生产检查清单、§8 安全模型为判断标准，不参考任何设计文档。

---

## 一、总体架构契合度评估矩阵

| SDK 说明书核心条款 | 契合度 | 说明 |
|--------------------|--------|------|
| §2 四大支柱接通率（4/4） | **50%** | 执行环境✅、上下文管理✅、委托⚠️（仅空壳）、控制❌（仅半套） |
| §4.2 中间件栈顺序正确性 | **100%** | `middleware=[]` 正确传入 create_deep_agent，SDK 内部按 12 层顺序排列，未打乱 |
| §4.3 虚拟文件系统安全模式 | **100%** | `virtual_mode=True` + `root_dir` 限定，严格符合 |
| §4.4 权限规则映射完整性 | **75%** | allow/deny 正确；`mode=interrupt` 由于 HITL 不通而实际失效 |
| §4.8 SubAgent 字段完整性 | **30%** | 仅传 name/description/system_prompt，缺 7 项 |
| §4.9 Memory(4 Kernel) + §4.10 Skills 映射 | **95%** | 4 个 Kernel 当 memory、skills 目录当 skills；只差 skills 的 mcp_json_path 可选参数 |
| §4.12 HITL 接通（3 个必要条件） | **33%** | 配了 checkpointer ✅、同 thread_id ✅、但没有 interrupts 检测/审批 API ❌ |
| §6 中间件扩展能力（第 7 位插入） | **10%** | 无全局中间件入口；per-agent 仅能靠 excluded_tools 白名单绕，未用公开 API |
| §7 生产部署检查清单（7 项） | **2/7** | checkpointer: SQLite OK 但并发风险；认证: 缺失；递归限制: 缺失；监控: 缺失；超时: 缺失；worker: 无 gunicorn 配置；thread_id: ✅ UUID |
| §8 安全最佳实践（6 项） | **4/6** | virtual_mode ✅、最小工具集 ⚠️、per-session thread ✅、密钥不进沙箱 ✅、HITL 分级 ❌、审计 ❌ |

**综合契合度得分：56 / 100**

---

## 二、符合 SDK 最佳实践的做法 ✅

### 2.1 Harness 层定位准确：SDK 调用零侵入、全量走公开 `create_deep_agent()`

整个 runtime 层没有任何"绕过 Harness 直接操作 LangGraph 组件"的代码。所有 Agent 实例化统一通过 `AgentFactory.create_agent()` → `deepagents.create_deep_agent(...)`，所有可配置项都通过 `create_kwargs` 传参。这完全符合 SDK §1.5 定义的三层架构边界（Harness 层屏蔽底层 LangGraph/LangChain 细节），保证未来 SDK 升级内部中间件调整时，本项目零修改适配。

### 2.2 虚拟文件系统配置完全符合 SDK §4.3 安全要求

`AgentFactory._create_backend()` 的 FilesystemBackend 配置：

```python
return FilesystemBackend(root_dir=root_path, virtual_mode=True)
```

1. `virtual_mode=True` 开启了路径逃逸防护（绝对路径、`..` 全部被重写）
2. `root_dir` 精确到 `.agentcore/workspace/agent/{agent_id}/`，per-agent 隔离
3. 没有任何全局 FilesystemBackend 共享实例

这三条和 SDK §4.3 "最佳实践推荐"条目一字不差。

### 2.3 Memory(4 个 Kernel 文件) + Skills(目录) 的分层映射精准

`Workspace.setup_initial_kernel_files()` 定义 4 个 Kernel 文件（agent / codebase / guidelines / instructions），`AgentFactory.create_agent()` 把它们按 SDK §4.9 "始终加载的 Memory" 传给 `memory=` 参数，把 `skills/` 目录按 §4.10 "渐进式披露的 Skills" 传给 `skills=` 参数。分层语义完全对齐 SDK 设计意图。

### 2.4 会话级 Thread ID 生成与检查点复用策略合规

- 新建 session → `configurable = {"thread_id": session.id}`
- 追加消息 → 同一个 session.id 反复使用

符合 §4.12 "HITL 必要条件 #2：相同 thread_id 才能恢复对话"以及 §7 检查清单 "Thread ID 唯一且稳定" 条款。

### 2.5 修改 Kernel 后检查点清理解到了 SDK 内部缓存行为

`invalidate_agent_graph()` 在删除本地 graph_cache 之后，还会调用 `checkpointer.adelete_thread()` 清理所有关联线程。这是对 SDK §4.9/§4.10 实现细节的深度正确理解 —— 因为 MemoryMiddleware 和 SkillsMiddleware 会把首次加载的 kernel 内容、技能元数据序列化进 checkpoint state，不删 checkpoint 就会导致编辑了 agent.md 但旧会话永远读不到更新。

### 2.6 子代理默认关闭、按需 opt-in，符合 §4.8 "最小化工具集"原则

通过 `settings.enable_subagents` 显式开关，且只有被设置成 `parent=True` 的 Agent 才能注册为子代理源。避免了"所有 Agent 互相可见"的 §8 安全红线问题。

### 2.7 摘要中间件阈值调优参数正确透传

正确读取 `settings.summarization_threshold` 并作为 `SummarizationMiddleware(token_threshold=X)` 传入 create_deep_agent。SDK §4.11 只给了默认值 85%，项目暴露这个配置是正确做法。

---

## 三、与 SDK 说明书存在偏差 / 需要改善的问题 ⚠️

以下所有问题均以 SDK 说明书明确条款为判定依据，不涉及任何外部设计期望。

---

### 🔴 严重问题（P0 — 违反 SDK 明确要求或导致功能静默失效）

#### 3.1 HITL（人机审批）只实现了 1/3，工具 `interrupt_on` 实际为死锁

**SDK §4.12 明确的 3 个必要条件：**
1. ✅ 配 checkpointer（已做）
2. ✅ 相同 thread_id（已做）
3. ❓ 检测 interrupts + 提供 `submit_decision` → **完全未实现**

当前实现的问题链：

| 位置 | 表现 | 后果 |
|------|------|------|
| `AgentFactory._map_interrupt_on()` | 只支持 `{tool: True}`，不支持 `InterruptOnConfig(allowed_decisions/when)` | 管理员无法限制"只能 approve，不能 edit 工具参数"等审批策略 |
| chat_router 路由层 | invoke 完成后直接把消息列表返回，**没有检查 `state.interrupts` 的存在** | Agent 遇到 interrupt 会挂起，但前端永远收不到中断通知，请求超时或无限等待 |
| 全局缺失 | 无 `POST /chat/interrupts/{id}/resolve`（对应 SDK `stream(interrupts=...)` 投影后的决策回写 API） | 即使前端检测到 interrupt，也没有 API 能把审批结果回写到 checkpointer |
| `mode=interrupt` 的 FilesystemMiddleware 权限规则 | 被正确解析并传入 | 但因上述 3 个缺失，实际表现是"Agent 尝试写/删文件 → 触发 interrupt → 挂起 → 用户无感知 → 超时" |

**本质**：§4.12 HITL 控制支柱接通率 33%，UI 上配置的任何审批规则最终表现为"Agent 挂死"。

---

#### 3.2 Checkpointer 全局共享 SQLite，违反 §7 "生产检查点按实例分离"精神

SDK §7 检查清单关于 checkpointer 的精神是：**隔离 + 并发安全**。当前：

```python
DB_PATH = DATA_DIR / "checkpoints.db"  # 单一全局文件
checkpointer = AsyncSqliteSaver.from_conn_string(f"sqlite+aiosqlite:///{DB_PATH}")
```

**具体问题（全部来源于 SQLite 特性 + SDK 说明书推荐）：**

1. **SQLite 只有库级写锁**：N 个 Agent 并发对话 → 写入序列化 → 超过 ~20 并发即出现 `SQLITE_BUSY: database is locked`。SDK §7 对 SqliteSaver 的定位是"中小规模生产"，前提是"每个实例独立 DB"或"用 PostgresSaver"；全局单 DB 等于把瓶颈放在最慢的组件上。
2. **无 Agent 维度的清理语义**：删除 Agent 时，checkpoints.db 中所有相关线程/检查点残留无法回收（当前代码没有按 agent_id 维度删行逻辑）。
3. **无法按 Agent 备份/迁移**：单个 Agent 的对话状态无法独立导出导入，只能整个 DB 操作。
4. **`invalidate_agent_graph()` 删除语义不安全**：按 thread_id 删检查点没有 agent_id 前缀限定，虽然 UUID 碰撞概率极低，但如果任何一个 session_id 生成逻辑出现碰撞（例如未来用了非 UUID 策略），会误删另一个 Agent 的会话状态。

---

#### 3.3 SubAgent 注册只填了 3/10 字段，成本优化与权限收窄两条 SDK 推荐策略完全不可用

SDK §4.8 "子代理完整字段参考" 列出 10 项：

| 字段 | SDK 说明 / 设计意图 | 当前实现 | 影响 |
|------|---------------------|----------|------|
| `name` | 唯一标识 | ✅ | — |
| `description` | LLM 委派路由依据 | ✅ 但模板化 (`f"Agent: {agent_id}"`) | 委派判断不准 |
| `system_prompt` | 子代理人格/身份 | ✅ | — |
| **`tools`** | 默认继承父代理，可显式设置收窄（最小工具集原则 §8） | ❌ 缺失 | 子代理拿到和父代理完全相同的工具（读/写/删/shell），**无法做权限收窄**（如：审计子代理只给只读工具） |
| **`model`** | 默认继承，可替换便宜模型（§4.8："按任务选模型 → 成本优化"） | ❌ 缺失 | 简单子任务（分类/摘要）跑 GPT-4o 级别模型，**成本无法降** |
| `middleware` | 不继承，可独立加 PII/审计等中间件 | ❌ 缺失 | 子代理无法独立加监控 |
| **`interrupt_on`** | 默认继承，可覆盖（子代理独立审批策略） | ❌ 缺失 | 父代理需要审批、子代理不需要（或反过来）的场景实现不了 |
| `skills` | 不继承，独立技能集 | ❌ 缺失 | 子代理独立技能集无法配置 |
| `response_format` | 结构化输出 | ❌ 缺失 | 低优先级 |
| **`permissions`** | 默认继承，可替换（权限收窄） | ❌ 缺失 | 和 tools 问题叠加，权限分层完全做不到 |

**当前实现**：`SubAgentRegistry.get_subagents()` 只返回 3 项。SDK §4.8 明确的"子代理成本优化策略"和"权限收窄策略"两条核心价值全部失效，子代理只剩"减少上下文 token"一个作用。

---

#### 3.4 依赖 SDK 私有 `_ToolExclusionMiddleware`，违反 SDK §6 "中间件公开扩展 API"原则

```python
try:
    from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware
except ImportError:
    _SdkToolExclusionMiddleware = None
```

**判定理由（SDK 说明书 §6）：**
- §6 明确规定"用户自定义中间件插入第 7 位"并使用公开 `BaseMiddleware` 基类
- 下划线前缀 = 私有 API，不在 SDK 公开契约内；任何 patch 版本升级可能删除/改名/移动
- 当 import 失败时 fallback 为 `None` → **工具排除功能静默失效**，管理员在 UI 上"禁用工具"的操作不生效，Agent 仍能调用被禁用工具（安全漏洞）

**SDK 说明书推荐的替代方案（§4.2 + §4.3）：** 使用公开的 `FilesystemMiddleware(tools=["allowed_tool_1", "allowed_tool_2"])` 白名单机制替代排除法，或通过 create_deep_agent 的公开参数 `excluded_tools=[...]`（如果 SDK 暴露这个公开参数 — 需验证，但绝对不能用私有 import）。

---

#### 3.5 沙箱后端被永久降级为 StateBackend，与 SDK §4.3 SandboxBackend 定义完全不符

```python
if backend_type == BackendType.SANDBOX:
    logger.info("Sandbox backend requested — using StateBackend (ephemeral)")
    return StateBackend()
```

**SDK §4.3 关于 SandboxBackend 的明确定义：**
> 提供隔离的 shell 执行环境 + 隔离工作区。使用 E2B / Daytona / LangSmith Sandbox。需要 API Key。

**当前实际行为对比：**
| 能力 | SDK 期望 SandboxBackend | 当前 StateBackend 降级 |
|------|-------------------------|------------------------|
| shell 执行（`sh` 工具） | ✅ 隔离沙箱中执行 | ❌ 完全没有（StateBackend 不提供 shell） |
| 工作区持久化 | ✅ 沙箱存储 | ❌ 内存临时，服务重启清空 |
| 文件读写工具 | ✅ 沙箱内 | ⚠️ 内存里读写（可运行但不持久） |
| 安全隔离 | ✅ 容器/VM 级 | ❌ 同进程 Python dict |

**核心问题**：UI 选择 "sandbox" 的用户期望的是**更安全的隔离执行**，实际拿到的是**比 FilesystemBackend 更弱、不持久化、还丢了 shell** 的临时后端。而且只有 info 级日志，UI 上无任何降级告警。这违反了 SDK §8 "能力声明必须准确"的原则（不能把一个能力弱得多的后端叫 sandbox）。

**正确做法**：sandbox 后端要么按 SDK §4.3 真接 E2B/Daytona/LangSmith Sandbox，要么在 UI dropdown 里移除这个选项，不能用 StateBackend 冒名顶替。

---

### 🟡 中等问题（P1 — 违反 SDK 推荐实践，不致命但显著降低能力/可运维性）

#### 3.6 缺少"第 7 位中间件"全局扩展入口（违反 SDK §6 中间件扩展架构）

SDK §6 架构图的核心设计：
```
  1 Skills → 2 Filesystem → 3-6 内置中间件 → **[第 7 位 用户自定义插入点]** → 8 Harness extras → ... → 12 HITL
```

这个第 7 位就是 deepagents 留给上层应用的扩展插槽（§6 例子：PII 脱敏、工具调用审计日志、Langfuse 追踪埋点都插在这里）。

当前 `AgentFactory.create_agent()` 完全没有提供**跨 Agent 的全局中间件注入机制**：

1. 没有配置入口（`controlplane.json` / `agent.json` 都没有 `global_middleware` 字段）
2. 没有注册接口（如 `app.register_middleware(MyMiddleware)`）
3. 结果：要给所有 Agent 加 PII 脱敏或审计日志中间件，**只能改 AgentFactory 代码硬编码**，无法热插拔

**对比 SDK 设计意图**：§6 明确说 Harness 层应该"提供中间件配置面板让业务快速接入"。当前相当于把这个插槽焊死了。

---

#### 3.7 ControlPlaneStore 内存全量 + 全量写盘 JSON，未达 §7 生产持久化要求

SDK §7 关于控制面状态管理的隐含要求（通过 checkpointer 对比推断）：**并发安全 + 可分片 + 按实体粒度操作**。

当前 `ControlPlaneStore` 架构问题：
1. **无并发写锁**：`_atomic_write_json` 是原子的但调用它之前没有互斥锁，两个 FastAPI 请求同时写 `agents.json` 是竞态条件（后写覆盖先写）。SDK §7 的 checkpointer 推荐 PostgresSaver 就是为了解决这个问题。
2. **O(N) 序列化**：每次更新一个 Agent 的 state，整个 `agent_states` dict（含全部 5 个字段 + 全部配置 snapshot）全部 re-serialize。Agent 数 ≥ 500 时这个延迟能到 100ms+ 量级，阻塞事件循环。
3. **全量驻内存**：所有 agent 配置 + 状态全部常驻内存，10k Agent 预估占用 200MB+。
4. **加载阻塞 startup**：lifespan 中 `await store.load()` 是同步 JSON 解析 + 构建全部 in-memory 对象，大文件会阻塞 ASGI 启动探针。

**建议对齐 §7 方案**：像 sessions 一样（已经分片成每 session 一个文件）把 agents 也拆成 `agents/{id}.json` + 索引，或直接引入 SQLAlchemy + SQLite/Postgres。

---

#### 3.8 API 层未实现 §7 认证要求，也无递归限制与超时

SDK §7 生产检查清单 7 项，当前仅通过 2 项：

| §7 检查项 | 状态 | 说明 |
|-----------|------|------|
| Checkpointer: MemorySaver→Postgres/Sqlite | ⚠️ 半通过 | 用了 SqliteSaver，但单 DB 并发问题（见 3.2） |
| Thread ID 唯一稳定 | ✅ | UUID4 hex |
| **认证: JWT / API Key / OAuth** | ❌ | 所有 router 无 `Depends(get_current_user)` 依赖项，0 认证 |
| **递归限制 sys.setrecursionlimit ≥ 100** | ❌ | 全局无设置；Python 默认 1000 够用但 §7 明确建议显式调高到 100-10000 区间，因为复杂子代理嵌套容易爆栈 |
| Worker: gunicorn + UvicornWorker | ❌ | 无 gunicorn 配置；pyproject.toml 也无 gunicorn 依赖 |
| **超时控制（recursion_limit + 请求超时）** | ❌ | invoke 时未传 `recursion_limit=N`（SDK §4.13 推荐显式设置），ASGI 无 timeout middleware |
| 监控: LangSmith / Langfuse | ❌ | 未接入；也没有通过第 7 位中间件插入 tracing（见 3.6） |

其中**认证缺失**在多租户或暴露内网时是零容忍问题，**递归限制 + 超时缺失**在复杂子代理递归委派时直接是稳定性炸弹。

---

#### 3.9 AgentRuntime 全局单锁导致 N Agent 并发串行化

```python
async with self._lock:  # AgentRuntime 单例的一把全局锁
    instance = self._get_or_raise(agent_id)
    if instance.state != AgentState.RUNNING: ...
    agent_graph = instance.agent
```

不同 agent_id 之间完全没有资源竞争，但这把全局锁导致 100 个 Agent 并发聊天也要排队通过状态检查。**这不是 SDK 明确要求的，但和 SDK §7 "横向扩展" 精神相悖**。MultiAgentManager 已经实现了 per-agent 锁 `_agent_locks`，直接复用模式即可。

---

#### 3.10 流式能力缺失（§4.13 `stream_events` + `subagents` 投影完全未利用）

SDK §4.13 是 deepagents 与"普通 LangGraph 封装"区别最大的特性：`stream(mode=["messages", "custom", "interrupts", "subagents"])` + `stream_events(version="v3")` 投影。

当前 chat_router 只有一个同步 POST 返回最终 message 列表。缺失：
- SSE/WS 流式 endpoint（打字机效果必需）
- `interrupts` 流投影（见 3.1 HITL 问题的前端通知）
- `subagents` 流投影（父代理委派给子代理 A 又委派给子代理 B，前端看不见这个委派链，调试时完全黑盒）
- Token usage 流事件（§4.14 PromptCache 命中也靠流事件暴露）

---

### 🟢 轻度问题（P2 — 小瑕疵 / 代码异味，不影响核心能力）

#### 3.11 模块级全局单例与 app.state 单例双份并存，潜在一致性问题

先在模块级创建 `store / factory / agent_manager / service`，然后在 `lifespan` 又 new 一份挂到 `app.state.*`。

- 模块级那份被 `ModelRouter` / `EnvRouter` 直接 import 使用
- app.state 那份被 agent/chat router 通过 Depends 注入
- 多 worker 时每份进程一份 in-memory store，天然不一致（§7 推荐多 worker 时用共享存储，这一条是叠加问题）

#### 3.12 `_atomic_write_json` 重复实现两次

- `repository.py` 一份
- `workspace.py` 一份

逻辑完全相同（mkstemp → dump + fsync → replace），应抽取到 `runtime.utils`。

#### 3.13 ModelFactory 环境变量优先级与 SDK §3 惯例相反

```
AGENTCORE_LLM_API_KEY → DASHSCOPE_API_KEY → QWEN_API_KEY → OPENAI_API_KEY
```

SDK §3 "3 步快速开始"惯例是**厂商原生 env var 优先**（`OPENAI_API_KEY` / `DASHSCOPE_API_KEY`），因为这是用户最可能在系统级别已配置的。AGENTCORE_* 前缀作为 fallback 更合适，当前反过来了。

#### 3.14 pyproject.toml deepagents 依赖是绝对本地 file 路径

```
"deepagents @ file:///D:/code/deepagents/libs/deepagents"
```

换机器、CI、Docker 构建直接失败。应该用 git URL + tag、或私有 PyPI。

---

## 四、架构清晰度专项评估（纯 SDK 视角）

| 维度 | 1-10 | 判定依据（SDK 说明书章节） |
|------|------|--------------------------|
| **Harness 层边界纯度** | 9/10 | 全量走 `create_deep_agent()`，零 LangGraph 直操（§1.5） |
| **四大支柱接通率** | 5/10 | 执行环境✅、上下文管理✅、委托⚠️空壳、控制❌半套（§2） |
| **中间件机制合规度** | 6/10 | 顺序对✅；但第 7 位扩展口死焊❌、私有 import❌（§6） |
| **文件系统安全合规** | 10/10 | virtual_mode + root_dir per-agent 一字不差（§4.3 + §8） |
| **上下文分层 (Memory/Skills) 正确度** | 9/10 | 4 Kernel memory + skills 目录映射精准（§4.9/§4.10） |
| **生产部署就绪度（§7 七项）** | 3/10 | 仅 thread_id ✅ + checkpointer ⚠️；认证/递归/超时/监控/gunicorn 全缺 |
| **安全最佳实践覆盖（§8 六项）** | 6/10 | virtual_mode/最小化/thread 隔离/密钥不出沙箱 ✅；分级 HITL/审计 ❌ |
| **可扩展性（§6 中间件 + §4.8 子代理）** | 3/10 | 全局中间件无入口；子代理 7/10 字段缺失；无 MCP 全局注册中心 |

**综合架构清晰度 / SDK 对齐度：6.1 / 10**

> **评语**：Harness 层骨架搭得**非常正确**（零侵入、中间件顺序正确、文件系统安全丝毫不差），属于"会用 SDK"的实现。但四大支柱里委托和控制两根支柱半残、§7 生产检查 5/7 缺项、§6 扩展插槽焊死、§4.8 子代理只填了 3 项。相当于买了一辆 4 驱车，前差速器和 4 个门的内饰都装了，但后差速器壳子空着没装齿轮、ESP 没接传感器、音响喇叭只接了 3 根线 —— 能开，但 4×4、主动安全、娱乐系统三大卖点都没发挥。

---

## 五、分阶段改进路线图（纯 SDK 说明书对齐）

### Phase 1 — 先修死锁和静默失效（一周内）

| # | 改进点 | 对应 SDK 条款 | 收益 |
|---|--------|---------------|------|
| 1 | **HITL 完整接通**：检测 `state.interrupts` 返回前端、加 `POST /chat/interrupts/{sid}/resolve` API、支持 `InterruptOnConfig(allowed_decisions/when)` | §4.12 三项必要条件 | 审批策略从"挂死"变"可用" |
| 2 | **SubAgent 字段补齐**：tools（可收窄）、model（成本优化）、interrupt_on（独立审批）、permissions 4 项核心先补全，剩下的 middleware/skills 二期 | §4.8 完整字段参考 + §8 最小化工具集 | 委派从"空壳"变成"能降成本+能收权限" |
| 3 | **沙箱后端二选一**：要么真接 E2B/Daytona/LangSmith（按 §4.3），要么移除 sandbox dropdown 选项，不能用 StateBackend 冒名 | §4.3 SandboxBackend 定义 | 能力声明准确，不误导用户 |
| 4 | **替换私有中间件 import**：用 §6 公开 API `FilesystemMiddleware(tools=[...])` 白名单或 create_deep_agent 公开 excluded_tools 参数替代 `_ToolExclusionMiddleware` | §6 公开扩展 API | 升级 SDK 不静默失效 |

### Phase 2 — 生产就绪（2-3 周）

| # | 改进点 | 对应 SDK 条款 |
|---|--------|---------------|
| 5 | **Checkpointer 按 Agent 分 DB 或改 Postgres** | §7 Checkpointer 推荐 |
| 6 | **ControlPlaneStore 分片或改 SQL**（agents/ 每文件） | §7 生产持久化隐含要求 + sessions 已走先例 |
| 7 | **认证**：加 API Key / JWT 依赖注入到所有 router | §7 检查清单三选一 |
| 8 | **递归限制 + 请求超时**：`sys.setrecursionlimit(5000)` + `recursion_limit=100` 透传 + ASGI timeout | §7 检查清单 + §4.13 |
| 9 | **全局中间件扩展入口**：`ControlPlaneStore.global_middleware` 配置 + AgentFactory 合并插入第 7 位 | §6 第 7 位插入点 |
| 10 | **Store 加写锁** + AgentRuntime 改 per-agent 锁 | §7 并发安全精神 |

### Phase 3 — 能力补全（后续）

| # | 改进点 | SDK 条款 |
|---|--------|----------|
| 11 | **SSE 流式 endpoint** + `messages/interrupts/subagents/token_usage` 四路投影 | §4.13 stream/mode 组合 |
| 12 | **监控**：LangSmith / Langfuse（第 7 位中间件接入，利用 Phase 2 #9） | §7 检查清单 |
| 13 | **gunicorn 多 worker 部署配置** + 共享存储（依赖 #5 #6） | §7 检查清单 |
| 14 | **SubAgent description 从 kernel/description 字段动态生成**，而非模板字符串 | §4.8 委派路由准确性 |
| 15 | ModelFactory env var 优先级调整为"厂商原生 → AGENTCORE fallback" + 去重 util 代码 | §3 + 代码质量 |
| 16 | **启用任务规划能力**：在 `AgentFactory` 中间件列表加入 `TodoListMiddleware`（opt-in），使 Agent 获得 `write_todos` 工具，复杂多步任务结构化分解 | §4.7 任务规划（当前未启用） |

---

## 六、一句话总结

> **以 SDK 说明书为唯一标尺：这个项目的 Harness 层架构边界和安全底座做得相当扎实（文件系统、Memory/Skills 分层、零侵入调用 100% 对齐），但"四大支柱"有两根半残（委托空壳、控制半套、扩展插槽焊死），§7 生产检查清单七项缺五项，§4.8 子代理十项缺七项。好消息是骨架完全对，修的都是"填空"而不是"拆了重建"——把 Phase 1 的 4 项修完，SDK 核心价值就能从 56 分拉到 80+ 分。**
