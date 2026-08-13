# Deep Agents 完全说明书

> **官方文档**: https://docs.langchain.com/oss/python/deepagents/overview
> **GitHub 仓库**: https://github.com/langchain-ai/deepagents
> **PyPI**: https://pypi.org/project/deepagents/
> **API 参考**: https://reference.langchain.com/python/deepagents
> **许可**: MIT
> **Python 版本要求**: >=3.11
> **当前版本**: 0.7.5（2026 年 8 月）

---

## 目录

- [0. 写在前面：为什么需要 Deep Agents](#0-写在前面为什么需要-deep-agents)
  - [学习路径建议](#学习路径建议)
- [1. 项目概述](#1-项目概述)
- [2. 整体架构与内核机制](#2-整体架构与内核机制)
- [3. 安装与快速开始](#3-安装与快速开始)
- [4. 核心功能详解](#4-核心功能详解)
  - [4.1 自定义工具 (Custom Tools)](#41-自定义工具-custom-tools)
  - [4.2 MCP 工具集成 (MCP Tools)](#42-mcp-工具集成-mcp-tools)
  - [4.3 虚拟文件系统 (Virtual Filesystem)](#43-虚拟文件系统-virtual-filesystem)
  - [4.4 文件系统权限 (Permissions)](#44-文件系统权限-permissions)
  - [4.5 代码沙箱 (Sandboxes)](#45-代码沙箱-sandboxes)
  - [4.6 解释器 (Interpreters / QuickJS)](#46-解释器-interpreters--quickjs)
  - [4.7 任务规划 (Task Planning / TodoList)](#47-任务规划-task-planning--todolist)
  - [4.8 子代理 (Subagents)](#48-子代理-subagents)
    - [子代理调用机制：主 Agent 如何调用子代理？](#子代理调用机制主-agent-如何调用子代理)
    - [通信协议：不是 A2A，是进程内调用](#通信协议不是-a2a是进程内调用)
    - [异步子代理 (Async Subagents)](#异步子代理-async-subagents)
  - [4.9 技能系统 (Skills)](#49-技能系统-skills)
  - [4.10 长期记忆 (Memory)](#410-长期记忆-memory)
  - [4.11 上下文工程 (Context Engineering)](#411-上下文工程-context-engineering)
  - [4.12 人机交互 (Human-in-the-Loop)](#412-人机交互-human-in-the-loop)
  - [4.13 事件流式输出 (Event Streaming)](#413-事件流式输出-event-streaming)
- [5. 其他能力](#5-其他能力)
- [6. 中间件架构详解](#6-中间件架构详解)
- [7. 推荐部署方案](#7-推荐部署方案)
  - [自托管部署](#自托管部署)
  - [端到端综合示例：多代理研究助手](#端到端综合示例多代理研究助手)
- [8. 安全模型与最佳实践](#8-安全模型与最佳实践)
- [9. 选型对比：何时使用 Deep Agents](#9-选型对比何时使用-deep-agents)
- [附录：完整参数参考](#附录完整参数参考)

---

## 0. 写在前面：为什么需要 Deep Agents

如果你用过最简单的 Agent——把 LLM 放进一个循环里，让它反复调用工具直到完成任务——你大概率遇到过这样的情况：任务跑了十几步之后，Agent 开始遗忘最初的目标，重复调用已经用过的工具，或者在大量工具返回结果的堆积中迷失方向。这就是所谓的"浅层陷阱"。

Deep Agents 的诞生正是为了解决这个问题。它的设计灵感来自 Claude Code、Manus 和 Deep Research 等已经被验证过的应用架构。LangChain 团队识别出了让这些应用成功的四个关键模式，并将其系统化地封装为一个通用框架。

### 传统 Agent 的困境

一个裸的 Agent 循环（LLM + tools + while loop）在面对长周期复杂任务时，会遇到五个层层叠加的问题：

**规划缺失**——裸循环走一步看一步。当任务需要"先做 A，再做 B，最后验证 C"时，Agent 可能在 B 之后忘了 C 是什么。没有显式的任务分解机制，多步任务的完成率会急剧下降。

**上下文膨胀**——每次工具调用的结果都追加到对话历史中。一个搜索工具返回 5000 token，一个文件读取返回 8000 token，十步之后上下文里塞满了中间结果，token 成本激增，模型的注意力也被稀释。

**遗忘与混乱**——当对话超过 10-20 步，LLM 开始忘记初始指令，重复已经完成的步骤，或者陷入死循环。这不是模型不够聪明，而是上下文窗口的物理限制。

**协作编排复杂**——当任务需要不同领域的专门处理时（比如先搜索、再分析、最后写报告），单个 Agent 难以兼顾所有领域的指令。多 Agent 编排需要处理上下文隔离、结果汇总、错误传播等复杂问题。

**环境交互困难**——让 Agent 安全地读写文件、执行代码是一个棘手的问题。直接给 Agent shell 权限太危险，完全不给又限制了能力。需要一种可插拔的、安全的执行环境。

### Deep Agents 的四大支柱

Deep Agents 用四个架构组件系统化地解决上述问题，这四个组件构成了理解整个框架的核心心智模型：

**执行环境（Execution Environment）**——Agent 能做什么。包括自定义工具、MCP 集成、虚拟文件系统（作为上下文缓冲区而非单纯存储）、权限控制、代码沙箱和流式输出。文件系统是这里的创新核心：它不仅是存储，更是一个上下文管理工具——大型工具结果自动写入文件，上下文中只保留路径引用。

**上下文管理（Context Management）**——Agent 知道什么。包括按需加载的技能（渐进式披露）、始终加载的记忆（AGENTS.md）、自动摘要与卸载（压缩历史、转存大结果到文件）、以及提示缓存。这些机制让 Agent 在有限的上下文窗口内高效运作。

**委托（Delegation）**——Agent 如何分工。包括结构化的任务规划（write_todos 工具）和子代理委派（task 工具）。子代理在隔离的上下文窗口中执行重活，只把最终结果返回给主 Agent，从根本上解决上下文膨胀。

**控制（Steering）**——人类如何介入。通过 interrupt_on 参数，在关键决策点暂停 Agent，等待人类审批、编辑或拒绝工具调用。这让 Agent 可以安全地执行高风险操作。

### 心智模型：三层理解 Deep Agents

要真正理解 Deep Agents，可以从三个层次切入：

**第一层：使用者视角**——`create_deep_agent(model=..., tools=..., system_prompt=...)` 返回一个可 `.invoke()` 的 LangGraph 图。你传入模型、工具和指令，它返回一个能自主规划、读写文件、委派子任务的智能体。这是大多数用户需要的全部。

**第二层：架构师视角**——Deep Agents 是一组中间件的组合。每个中间件负责一个能力：`FilesystemMiddleware` 提供文件工具，`SubAgentMiddleware` 提供子代理委派，`SummarizationMiddleware` 提供上下文压缩。你可以替换、移除或添加任何中间件。`create_deep_agent` 本质上是把这些中间件按特定顺序组装到 LangGraph 的 `create_agent` 之上。

**第三层：内核视角**——一切最终运行在 LangGraph 的 StateGraph 上。状态（messages, files, todos 等）在图的节点间流转，检查点器（checkpointer）持久化每一步状态，中断（interrupt）暂停图执行等待外部输入，流式（streaming）投影图执行事件。理解这一层，你就能解释 Deep Agents 的所有行为——从为什么 HITL 需要 checkpointer，到为什么子代理能隔离上下文。

### 学习路径建议

本文档按照"先全局、再细节、后内核"的顺序组织，建议按以下路径学习：

**入门（1-2 小时）**——读第 0-3 节，建立全局观。跑通快速开始示例，理解四大支柱和三层定位。目标：能创建一个带自定义工具的 Agent 并运行。

**进阶（3-5 小时）**——按需选读第 4 节的子章节。建议顺序：4.1 工具 → 4.3 文件系统 → 4.8 子代理 → 4.11 上下文工程 → 4.12 人机交互。每个子章节都有独立可运行的示例。目标：掌握核心能力的配置和使用。

**深入（2-3 小时）**——读第 2 节的中间件栈和上下文流转、第 6 节中间件架构详解。目标：理解内部机制，能编写自定义中间件，能解释 Agent 的每个行为背后的原理。

**实战（2-3 小时）**——读第 7-9 节，结合附录参数参考。目标：能在生产环境部署 Agent，能做出正确的选型决策。

| 阶段 | 读哪些节 | 关键问题 | 产出 |
|------|---------|---------|------|
| 入门 | 0, 1, 2, 3 | Deep Agents 是什么？怎么跑起来？ | 一个能运行的 Agent |
| 进阶 | 4.1-4.13 | 怎么用文件系统、子代理、HITL？ | 功能完整的 Agent |
| 深入 | 2（详解）, 6 | 中间件栈怎么工作？上下文怎么流转？ | 自定义中间件 |
| 实战 | 7, 8, 9, 附录 | 怎么部署？怎么保证安全？ | 生产级应用 |

---

## 1. 项目概述

**Deep Agents** 是 LangChain 团队开源的通用"深度智能体"框架（agent harness），构建在 **LangChain**（框架层）和 **LangGraph**（运行时层）之上。它是一个"开箱即用、可扩展"的智能体框架——默认配置已经针对长周期、多步骤任务调优，同时允许你覆盖、替换任何组件而无需 fork 代码。

### 三层定位

| 层级 | 名称 | 职责 |
|------|------|------|
| **Runtime** | LangGraph | 持久化、可观测的图执行运行时，提供 streaming、checkpoint、HITL |
| **Framework** | LangChain | 高层 Agent 接口 (`create_agent`) + 中间件机制 |
| **Harness** | Deep Agents | 在上述基础上进一步封装，内置规划、文件系统、子代理、记忆等"深度"能力 |

这三层是组合关系而非替代关系。任何 LangGraph `CompiledStateGraph` 都可以作为子代理传入 Deep Agent，所以自定义编排可以与框架的默认能力并存。

### 核心解决痛点

Deep Agents 通过模块化中间件架构，将传统 Agent 的五大痛点系统化解决：

1. **规划缺失** → `write_todos` 工具 + `TodoListMiddleware`，结构化任务分解
2. **上下文膨胀** → 文件系统卸载 + `SummarizationMiddleware` + 子代理隔离
3. **遗忘与混乱** → 自动摘要保留会话意图 + 记忆系统持久化关键指令
4. **协作编排复杂** → `task` 工具 + `SubAgentMiddleware`，隔离上下文的子任务委派
5. **环境交互困难** → 可插拔后端 + 权限控制 + 沙箱执行

---

## 2. 整体架构与内核机制

### 四大架构组件

```
┌─────────────────────────────────────────────────────────────────┐
│                    Deep Agents 整体架构                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────┐ │
│  │  1. 执行环境  │  │ 2. 上下文管理 │  │  3. 委托     │  │ 4. 控制 │ │
│  │  Execution   │  │  Context    │  │  Delegation │  │Steering│ │
│  │  Environment │  │  Management │  │             │  │        │ │
│  ├─────────────┤  ├─────────────┤  ├─────────────┤  ├────────┤ │
│  │ • 工具 & MCP │  │ • Skills    │  │ • 任务规划   │  │ • HITL │ │
│  │ • 虚拟文件系统│  │ • Memory    │  │   (Todos)   │  │  审批  │ │
│  │ • 权限控制   │  │ • 摘要&卸载  │  │ • 子代理     │  │        │ │
│  │ • 代码执行   │  │ • 提示缓存   │  │   (Task)    │  │        │ │
│  │ • 流式输出   │  │             │  │             │  │        │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────┘ │
│                                                                 │
│  ─────────────  底层运行时  ─────────────                         │
│  LangGraph StateGraph (持久化 · 检查点 · 流式 · 人机交互)          │
│  LangChain (工具集成 · 模型适配 · 中间件)                          │
└─────────────────────────────────────────────────────────────────┘
```

### 架构组件说明

| 组件 | 子模块 | 作用 |
|------|--------|------|
| **执行环境** | Tools & MCP | 自定义函数、API、数据库、MCP 服务器 |
| | 虚拟文件系统 | 可插拔后端的文件读写（ls, read_file, write_file 等） |
| | 权限控制 | 声明式路径级访问控制 |
| | 代码执行 | 沙箱（execute 工具）+ 解释器（eval 工具/QuickJS） |
| | 流式输出 | 类型化事件流，包括子代理流 |
| **上下文管理** | Skills | 按需加载的领域知识（渐进式披露） |
| | Memory | 持久化指令/偏好（AGENTS.md） |
| | 摘要与卸载 | 自动压缩历史 + 大结果转存文件 |
| | 提示缓存 | Anthropic/Bedrock 模型自动缓存 |
| **委托** | 任务规划 | write_todos 工具，结构化待办列表 |
| | 子代理 | task 工具，隔离上下文的子任务执行 |
| **控制** | 人机交互 | interrupt_on 参数，关键决策暂停审批 |

### 中间件栈：请求是如何被处理的

理解 Deep Agents 的关键在于理解中间件栈。`create_deep_agent` 内部做的事情，本质上就是把一组中间件按特定顺序组装到 LangChain 的 `create_agent` 之上。每个中间件可以：在 Agent 执行前注入工具或修改系统提示（`before_agent`）、在每次模型调用前修改消息或工具列表（`before_model`）、在每次模型调用后处理响应（`after_model`）、拦截工具调用（`wrap_tool_call`）。

中间件执行顺序（从先到后）：

```
1. SkillsMiddleware          ← 仅当传入 skills 时：注入技能工具和提示
2. FilesystemMiddleware      ← 文件系统操作 + 权限执行（核心，不可移除）
3. SubAgentMiddleware        ← 仅当至少有一个同步子代理时：注入 task 工具
4. SummarizationMiddleware   ← 上下文压缩：在模型调用前检查并压缩历史
5. PatchToolCallsMiddleware  ← 修复中断后的悬空工具调用（维护消息一致性）
6. AsyncSubAgentMiddleware   ← 仅当配置了异步子代理时
7. 用户自定义 middleware      ← 在此位置插入
8. Harness Profile extras    ← 模型特定中间件
9. 工具过滤                  ← 移除 profile 中排除的工具
10. 提示缓存中间件            ← AnthropicPromptCaching / BedrockPromptCaching
11. MemoryMiddleware         ← 仅当传入 memory 时：加载 AGENTS.md 到系统提示
12. HumanInTheLoopMiddleware ← 仅当传入 interrupt_on 时：拦截工具调用等待审批
```

你通过 `middleware=` 参数传入的中间件，会插入到第 7 的位置——在核心中间件之后、profile 和提示缓存之前。如果你传入的中间件实例的 `.name` 属性与某个默认中间件匹配，它会**原地替换**那个默认中间件，而不是追加。这是覆盖默认行为的标准方式。

### 一次完整的 Agent 轮次是如何执行的

当调用 `agent.invoke({"messages": [...]})` 时，内部发生的事情：

1. **状态初始化**——LangGraph 从检查点恢复状态（如果是续接对话），或创建新状态。状态包含 `messages`（对话历史）、`files`（虚拟文件系统内容）、`todos`（待办列表）等字段。

2. **中间件 before_agent**——每个中间件的 `before_agent` 钩子按顺序执行。`SkillsMiddleware` 注入技能加载工具，`FilesystemMiddleware` 注入文件系统工具，`SubAgentMiddleware` 注入 `task` 工具，`MemoryMiddleware` 将 `AGENTS.md` 内容追加到系统提示。

3. **Agent 循环开始**——进入 LangGraph 的 agent 节点，这是一个 `while` 循环：调用模型 → 检查是否有工具调用 → 执行工具 → 回到调用模型。

4. **中间件 before_model**——在每次模型调用前，`SummarizationMiddleware` 检查上下文长度。如果超过阈值（模型窗口的 85%），先执行卸载或摘要。其他中间件可以在此修改消息列表或工具列表。

5. **模型调用**——LLM 接收系统提示 + 消息历史 + 可用工具列表，返回响应（文本或工具调用）。

6. **中间件 after_model**——处理模型响应。`PatchToolCallsMiddleware` 检查是否有因中断而未完成的工具调用，修复消息历史。

7. **工具执行**——如果模型返回工具调用，执行工具节点。`HumanInTheLoopMiddleware` 在此拦截需要审批的工具调用，暂停执行。`FilesystemMiddleware` 执行文件操作并检查权限。

8. **循环判断**——如果还有工具调用，回到步骤 4。如果模型返回纯文本（没有工具调用），循环结束。

9. **中间件 after_agent**——所有中间件的 `after_agent` 钩子执行，做最终清理。

10. **状态持久化**——检查点器保存最终状态。如果是中断状态，保存中断点以便后续恢复。

### 上下文如何流转

Deep Agents 的上下文管理是一个多层管道，理解它对构建高效 Agent 至关重要：

```
用户输入
    │
    ▼
┌──────────────────┐
│  系统提示组装      │  ← 自定义 prompt + 内置 prompt + Memory(AGENTS.md) + Skills 提示 + 工具提示
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  before_model     │  ← SummarizationMiddleware 检查上下文长度
│  上下文检查        │     超过 85% → 卸载大结果到文件 → 摘要历史
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  模型调用          │  ← 发送系统提示 + 压缩后的消息历史 + 工具列表
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  工具执行          │  ← 大结果(>20k tokens)自动写入文件，上下文保留路径引用
└────────┬─────────┘
         │
         ▼
    回到 before_model（如果还有工具调用）
```

关键在于：文件系统不仅是存储工具，更是上下文管理的核心机制。当工具返回大量数据时，这些数据被自动转存到虚拟文件系统中，上下文里只保留一个文件路径引用和前 10 行预览。Agent 需要时可以通过 `read_file` 分页读取。这就是 Deep Agents 能处理长周期任务而不爆上下文的根本原因。

---

## 3. 安装与快速开始

### 前置条件

- Python >= 3.11
- 一个支持工具调用（tool calling）的 LLM。可以使用 Ollama 本地模型（免费，无需 API 密钥），或任何需要 API 密钥的云服务商（OpenAI、Anthropic、Google Gemini 等）

### 安装

```bash
# pip
pip install deepagents

# uv（推荐，速度更快）
uv add deepagents

# poetry
poetry add deepagents
```

根据你使用的模型供应商，还需要安装对应的 LangChain 集成包：

```bash
# OpenAI
pip install -U "langchain[openai]"

# Anthropic
pip install -U "langchain[anthropic]"

# Google Gemini
pip install -U "langchain[google-genai]"

# Ollama (本地模型，免费，无需 API 密钥)
pip install -U "langchain[ollama]"

# 安装 QuickJS 解释器支持（可选）
pip install -U "deepagents[quickjs]"
```

> **提示**：Ollama 在本地运行模型，完全免费且无需 API 密钥。先从 [ollama.com](https://ollama.com) 安装 Ollama，然后拉取模型（如 `ollama pull qwen3:8b`）即可使用。其他供应商（OpenAI、Anthropic、Google Gemini 等）则需要对应的 API 密钥。

### 快速开始

这是一个完整可运行的示例。使用 Ollama 本地模型，无需任何 API 密钥：

```python
# Ollama 本地运行，无需 API 密钥
# 先安装 Ollama: https://ollama.com
# 拉取模型: ollama pull qwen3:8b

from deepagents import create_deep_agent


def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"


agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[get_weather],
    system_prompt="You are a helpful assistant",
)

# 运行 agent
result = agent.invoke(
    {"messages": [{"role": "user", "content": "what is the weather in sf"}]}
)
print(result["messages"][-1].content)
# 输出类似: The weather in San Francisco is always sunny!
```

这个简单的调用背后发生了什么：`create_deep_agent` 创建了一个 LangGraph 图，内置了文件系统工具（ls, read_file, write_file 等）、一个 `general-purpose` 子代理、以及上下文管理中间件。即使你只传入一个自定义工具，Agent 也能读写文件、委派子任务、自动管理上下文。

### 支持的模型供应商

Deep Agents 是模型无关的——任何支持工具调用的 LLM 都可以使用。通过 `provider:model` 格式指定模型：

| 供应商 | 安装 | 模型字符串 |
|--------|------|-----------|
| OpenAI | `pip install -U "langchain[openai]"` | `openai:gpt-5.5` |
| Anthropic | `pip install -U "langchain[anthropic]"` | `anthropic:claude-sonnet-4-6` |
| Google Gemini | `pip install -U "langchain[google-genai]"` | `google_genai:gemini-3.6-flash` |
| Azure OpenAI | `pip install -U "langchain[openai]"` | `azure_openai:gpt-5.5` |
| AWS Bedrock | `pip install -U "langchain[aws]"` | `anthropic.claude-sonnet-4-6`（需配合 `model_provider="bedrock_converse"`） |
| HuggingFace | `pip install -U "langchain[huggingface]"` | `microsoft/Phi-3-mini-4k-instruct`（需配合 `model_provider="huggingface"`） |
| OpenRouter | `pip install -U "langchain[openai]"` | `openrouter:z-ai/glm-5.2` |
| Fireworks | `pip install -U "langchain[openai]"` | `fireworks:accounts/fireworks/models/glm-5p2` |
| Baseten | 安装 baseten 集成 | `baseten:zai-org/GLM-5.2` |
| Ollama | `pip install -U "langchain[ollama]"` | `ollama:qwen3:8b`（推荐）或 `ollama:north-mini-code-1.0` |

你也可以传入已初始化的模型对象而非字符串：

```python
from langchain.chat_models import init_chat_model
from deepagents import create_deep_agent

# 方式 1：用 init_chat_model（推荐，自动处理参数）
model = init_chat_model("ollama:qwen3:8b")
agent = create_deep_agent(model=model)

# 方式 2：直接使用模型类
from langchain_ollama import ChatOllama
model = ChatOllama(model="qwen3:8b")
agent = create_deep_agent(model=model)
```

聊天模型会自动重试暂态 API 失败（指数退避）。模型字符串格式 `provider:model` 底层调用 `init_chat_model`，使用默认参数。如需自定义参数（temperature, max_tokens 等），请用 `init_chat_model` 或模型类直接创建。

---

## 4. 核心功能详解

### 4.1 自定义工具 (Custom Tools)

Deep Agents 可以调用你定义的任何工具：普通函数、`@tool` 装饰器函数、或工具字典。框架从函数签名和 docstring 自动推断工具 schema——参数类型成为 JSON Schema 的类型，docstring 成为工具描述，参数名后的类型注解成为参数描述。

```python
import os
from typing import Literal
from tavily import TavilyClient
from deepagents import create_deep_agent

# 设置 Tavily API 密钥
os.environ["TAVILY_API_KEY"] = "tvly-..."

tavily_client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])


def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
):
    """Run a web search.

    Use this tool when you need to find information on the internet.

    Args:
        query: The search query string
        max_results: Maximum number of results to return (default 5)
        topic: Search topic type - general, news, or finance
        include_raw_content: Whether to include raw page content in results
    """
    return tavily_client.search(
        query,
        max_results=max_results,
        include_raw_content=include_raw_content,
        topic=topic,
    )


agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[internet_search],
)
```

**工作原理**：当你传入一个普通函数时，LangChain 自动用 `@tool` 装饰它，从类型注解生成参数 schema，从 docstring 提取描述。模型看到的是工具名称、描述和参数 schema。清晰的描述和参数文档直接影响模型调用工具的准确性——务必在 docstring 中说明**何时**使用这个工具。

你还可以用 `@tool` 装饰器获得更多控制：

```python
from langchain.tools import tool

@tool(parse_docstring=True)
def search_orders(
    user_id: str,
    status: str,
    limit: int = 10,
) -> str:
    """Search for user orders by status.

    Use this when the user asks about order history or wants to check
    order status. Always filter by the provided status.

    Args:
        user_id: Unique identifier for the user
        status: Order status: 'pending', 'shipped', or 'delivered'
        limit: Maximum number of results to return
    """
    # 实现逻辑
    return f"orders for {user_id} with status {status} (limit {limit})"
```

`parse_docstring=True` 会从 Google 风格的 docstring 中提取参数描述，生成更精确的工具 schema。

**内置工具列表**（自动注入，无需手动添加）：

| 工具 | 说明 | 来源中间件 |
|------|------|-----------|
| `ls` | 列出目录文件及元数据（大小、修改时间） | FilesystemMiddleware |
| `read_file` | 读取文件内容（支持分页 offset/limit、多模态） | FilesystemMiddleware |
| `write_file` | 创建或覆盖文件 | FilesystemMiddleware |
| `edit_file` | 精确字符串替换（支持 `replace_all` 全局替换） | FilesystemMiddleware |
| `delete` | 递归删除文件或目录（需 >=0.7） | FilesystemMiddleware |
| `glob` | 按模式匹配查找文件（如 `**/*.py`） | FilesystemMiddleware |
| `grep` | 搜索文件内容（多种输出模式：仅文件名、带上下文、计数） | FilesystemMiddleware |
| `execute` | 运行 shell 命令（仅沙箱后端） | SandboxBackend |
| `task` | 派生子代理处理委派任务 | SubAgentMiddleware |
| `write_todos` | 任务规划（需通过 TodoListMiddleware 启用） | TodoListMiddleware |
| `eval` | JavaScript 执行（需通过 CodeInterpreterMiddleware 启用） | CodeInterpreterMiddleware |
| `compact_conversation` | 按需上下文压缩（需手动启用） | SummarizationToolMiddleware |

不需要文件系统工具时，可以通过 Harness Profile 的 `excluded_tools` 隐藏它们（不是移除中间件）：

```python
from deepagents import HarnessProfile, register_harness_profile

register_harness_profile(
    "ollama:qwen3:8b",
    HarnessProfile(
        excluded_tools=frozenset(
            {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep"}
        ),
    ),
)
```

> **注意**：不要通过 `excluded_middleware` 移除 `FilesystemMiddleware`——它是必需的框架支撑，移除会抛出 `ValueError`。用 `excluded_tools` 只隐藏模型可见的工具表面，中间件本身保留。

---

### 4.2 MCP 工具集成 (MCP Tools)

Deep Agents 完全支持 [Model Context Protocol (MCP)](https://docs.langchain.com/oss/python/langchain/mcp)——连接外部服务的开放标准。通过 `langchain-mcp-adapters` 连接 MCP 服务器，把外部能力（数据库、API、文件系统等）作为工具暴露给 Agent。

```bash
pip install langchain-mcp-adapters
```

```python
import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from deepagents import create_deep_agent


async def main():
    # 连接一个或多个 MCP 服务器
    client = MultiServerMCPClient(
        {
            "my_server": {
                "transport": "http",
                "url": "http://localhost:8000/mcp",
            }
        }
    )
    # 获取服务器暴露的工具列表
    tools = await client.get_tools()

    agent = create_deep_agent(
        model="ollama:qwen3:8b",
        tools=tools,
    )

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Use the MCP server to help me."}]},
        config={"configurable": {"thread_id": "1"}},
    )
    print(result["messages"][-1].content)


asyncio.run(main())
```

MCP 支持 stdio 传输（本地进程）、HTTP 传输（远程服务）、OAuth 认证、工具过滤和有状态会话。MCP 工具与自定义函数工具在使用上完全等价——模型看到的都是统一的工具 schema。

---

### 4.3 虚拟文件系统 (Virtual Filesystem)

文件系统是 Deep Agents 最核心的创新之一。它不是单纯的文件存储，而是一个**上下文缓冲区**：大型工具结果自动写入文件，Agent 上下文中仅保留路径引用和前几行预览。这使得 Agent 可以处理远超上下文窗口容量的数据。

#### 内置后端

| 后端 | 说明 | 适用场景 |
|------|------|---------|
| **StateBackend** (默认) | 存储在 LangGraph 状态中，线程内持久（通过 checkpointer），跨线程不共享 | 临时中间结果、大多数用例 |
| **FilesystemBackend** | 直接读写本地磁盘 | 本地开发 CLI、CI/CD |
| **LocalShellBackend** | 文件系统 + 无隔离的 shell 执行 | 本地开发（极度谨慎） |
| **StoreBackend** | 使用 LangGraph BaseStore，跨线程持久化 | 跨会话记忆 |
| **ContextHubBackend** | 存储在 LangSmith Hub repo 中 | LangSmith 原生持久化 |
| **CompositeBackend** | 路由器，按路径前缀路由到不同后端 | 混合存储（最灵活） |

#### 示例：StateBackend（默认）

```python
from deepagents import create_deep_agent
from deepagents.backends import StateBackend

# 默认就是 StateBackend，无需显式指定
agent = create_deep_agent(model="ollama:qwen3:8b")

# 显式指定（效果相同）
agent2 = create_deep_agent(
    model="ollama:qwen3:8b",
    backend=StateBackend(),
)
```

StateBackend 的工作方式：文件内容存储在 LangGraph 的图状态（`state["files"]`）中。通过 checkpointer，文件在同一个线程（thread）的多轮对话间持久化。子代理与主代理共享同一个 StateBackend——子代理写入的文件在子代理执行结束后仍然可用。

#### 示例：FilesystemBackend（本地磁盘）

```python
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    backend=FilesystemBackend(root_dir="/path/to/project", virtual_mode=True),
)
```

> **安全警告**: `virtual_mode=True` 配合 `root_dir` 启用路径限制（阻止 `..`、`~`、绝对路径越界）。默认 `virtual_mode=False` **无任何安全保护**——即使设了 `root_dir`，Agent 也能通过符号链接等方式访问其他路径。不要在 Web 服务器或 HTTP API 中使用 FilesystemBackend。

**最佳实践**：在大多数用例中，应该用 `CompositeBackend` 包装 `FilesystemBackend`。Deep Agents 会自动将内部数据（卸载的大型工具结果、对话历史归档）写入后端。如果直接使用 `FilesystemBackend`，这些内部文件会写入真实磁盘，与你的项目文件混在一起：

```python
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, FilesystemBackend

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    backend=CompositeBackend(
        default=StateBackend(),  # 内部数据保持临时
        routes={
            "/workspace/": FilesystemBackend(root_dir="/path/to/project", virtual_mode=True),
        },
    ),
)
```

#### 示例：StoreBackend（跨线程持久化）

```python
from deepagents import create_deep_agent
from deepagents.backends import StoreBackend
from langgraph.store.memory import InMemoryStore

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    backend=StoreBackend(
        namespace=lambda rt: (rt.server_info.user.identity,),  # 按用户隔离
    ),
    store=InMemoryStore(),  # 本地开发用；LangSmith 部署时自动提供
)
```

命名空间（namespace）决定了数据的隔离粒度。`rt`（runtime）对象包含 `server_info`（用户身份、assistant ID、线程 ID）和 `context`（自定义上下文）：

```python
# 按用户隔离——每用户独立文件空间
StoreBackend(namespace=lambda rt: (rt.server_info.user.identity,))

# 按 assistant 隔离——同 assistant 的所有用户共享
StoreBackend(namespace=lambda rt: (rt.server_info.assistant_id,))

# 按会话隔离——每个线程独立
StoreBackend(namespace=lambda rt: (rt.execution_info.thread_id,))
```

#### 示例：CompositeBackend（混合路由）

```python
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, FilesystemBackend, StoreBackend
from langgraph.store.memory import InMemoryStore

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    backend=CompositeBackend(
        default=StateBackend(),  # 默认：临时存储
        routes={
            "/workspace/": FilesystemBackend(root_dir="/path/to/project", virtual_mode=True),
            "/memories/": StoreBackend(namespace=lambda _rt: ("memories",)),
        },
    ),
    store=InMemoryStore(),
)
```

> **最佳实践**: 用 `CompositeBackend` 将内部数据（大型工具结果、对话历史）保持在 `StateBackend`（临时），将需要持久化的数据路由到 `StoreBackend`。

#### 自定义后端

实现 `BackendProtocol` 接口即可连接任何存储（数据库、对象存储、远程文件系统）：

```python
from deepagents.backends.protocol import (
    BackendProtocol, EditResult, GlobResult, GrepResult,
    LsResult, ReadResult, WriteResult,
)

class S3Backend(BackendProtocol):
    def __init__(self, bucket: str, prefix: str = ""):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")

    def _key(self, path: str) -> str:
        return f"{self.prefix}{path}"

    def ls(self, path: str) -> LsResult:
        ...

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        ...

    def write(self, file_path: str, content: str) -> WriteResult:
        ...

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        ...

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        ...

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        ...
```

> **注意**: 返回结构化结果类型（含 `error` 字段），**不要抛异常**。要支持 `execute` 工具，实现 `SandboxBackendProtocol`（扩展 `BackendProtocol` 增加 `execute` 方法）。

#### 多模态文件支持

`read_file` 工具原生支持以下非文本文件类型，返回多模态内容块：

| 类型 | 扩展名 |
|------|--------|
| 图片 | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.heic`, `.heif` |
| 视频 | `.mp4`, `.mpeg`, `.mov`, `.avi`, `.flv`, `.mpg`, `.webm`, `.wmv`, `.3gpp` |
| 音频 | `.wav`, `.mp3`, `.aiff`, `.aac`, `.ogg`, `.flac` |
| 文件 | `.pdf`, `.ppt`, `.pptx` |

---

### 4.4 文件系统权限 (Permissions)

声明式路径级访问控制，控制 Agent 可以读写哪些文件/目录。权限规则只作用于内置文件系统工具（`ls`, `read_file`, `glob`, `grep`, `write_file`, `edit_file`, `delete`），不影响自定义工具和 MCP 工具，也不影响沙箱后端的 `execute` 命令。

> **要求**: `deepagents>=0.5.2`

#### 规则结构

每条 `FilesystemPermission` 包含三个字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `operations` | `list["read" \| "write"]` | `"read"` 覆盖 ls/read_file/glob/grep；`"write"` 覆盖 write_file/edit_file/delete |
| `paths` | `list[str]` | Glob 模式，支持 `**` 递归和 `{a,b}` 交替 |
| `mode` | `"allow" \| "deny" \| "interrupt"` | 允许、拒绝或暂停等待人工审批 |

```python
from deepagents import FilesystemPermission, create_deep_agent

agent = create_deep_agent(
    model=model,
    backend=backend,
    permissions=[
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/workspace/**"],
            mode="allow",
        ),
    ],
)
```

**评估规则**: 按声明顺序评估，**第一个匹配的规则生效**（first-match-wins）。如果没有任何规则匹配，操作被**允许**（宽松默认）。

#### 示例：只读 Agent

```python
# 拒绝所有写操作
agent = create_deep_agent(
    model=model,
    backend=backend,
    permissions=[
        FilesystemPermission(
            operations=["write"],
            paths=["/**"],
            mode="deny",
        ),
    ],
)
```

#### 示例：隔离到工作区目录

```python
agent = create_deep_agent(
    model=model,
    backend=backend,
    permissions=[
        # 允许 /workspace/ 下的读写
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/workspace/**"],
            mode="allow",
        ),
        # 拒绝其他所有
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/**"],
            mode="deny",
        ),
    ],
)
```

#### 示例：保护特定文件

```python
agent = create_deep_agent(
    model=model,
    backend=backend,
    permissions=[
        # 先拒绝 .env 和 examples 目录
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/workspace/.env", "/workspace/examples/**"],
            mode="deny",
        ),
        # 再允许 workspace 下其他文件
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/workspace/**"],
            mode="allow",
        ),
        # 最后拒绝一切其他
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/**"],
            mode="deny",
        ),
    ],
)
```

> **规则顺序至关重要**: 由于 first-match-wins，更具体的规则必须放在更宽泛的规则之前。如果把 `/workspace/**` allow 放在 `/workspace/.env` deny 前面，deny 规则永远不会被触发。

#### 暂停审批模式 (interrupt)

> **要求**: `deepagents>=0.6.8`

```python
from langgraph.checkpoint.memory import MemorySaver

agent = create_deep_agent(
    model=model,
    permissions=[
        FilesystemPermission(
            operations=["write"],
            paths=["/secrets/**"],
            mode="interrupt",  # 暂停等待人工审批
        ),
    ],
    checkpointer=MemorySaver(),  # interrupt 模式需要 checkpointer
)
```

interrupt 模式的规则会自动接入 HITL 中间件，与 `interrupt_on` 配置合并。恢复方式与工具调用中断相同（见 [4.12 人机交互](#412-人机交互-human-in-the-loop)）。

> **锚定规则**: interrupt 模式的路径模式必须以字面前缀段锚定（如 `/secrets/**` 或 `/projects/*/secrets/**`）。批量工具（`ls`、`glob`、`grep`、以及目录级 `delete`）在搜索子树可能重叠规则锚定前缀时触发中断。完全未锚定的模式（如 `/**/secrets`）会导致这些工具**过度触发**中断——因为它们的搜索范围理论上可能覆盖任意路径下的 secrets 目录。

> **注意**: `delete` 工具对目录采用全有或全无策略——检查目标及所有子路径的权限，任何一个被拒绝则整个操作拒绝。对普通文件采用精确匹配（first-match-wins）。`delete` 的精确匹配行为需要 `deepagents>=0.7.3`。

#### 子代理权限

子代理默认继承父代理的权限。在子代理 spec 中设置 `permissions` 字段会**完全替换**父代理的规则（不是合并）：

```python
agent = create_deep_agent(
    model=model,
    backend=backend,
    permissions=[
        FilesystemPermission(operations=["read", "write"], paths=["/workspace/**"], mode="allow"),
        FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny"),
    ],
    subagents=[
        {
            "name": "auditor",
            "description": "Read-only code reviewer",
            "system_prompt": "Review the code for issues.",
            "permissions": [
                FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
                FilesystemPermission(operations=["read"], paths=["/workspace/**"], mode="allow"),
                FilesystemPermission(operations=["read"], paths=["/**"], mode="deny"),
            ],
        }
    ],
)
```

#### CompositeBackend + 沙箱的特殊限制

当 `CompositeBackend` 的 default 是沙箱时，权限路径必须限定在已知的路由前缀下。因为沙箱支持任意命令执行，路径限制无法阻止通过 shell 命令访问文件系统：

```python
from deepagents.backends import CompositeBackend

composite = CompositeBackend(
    default=sandbox,
    routes={"/memories/": memories_backend},
)

# 正确：权限限定在 /memories/ 路由
agent = create_deep_agent(
    model=model,
    backend=composite,
    permissions=[
        FilesystemPermission(operations=["write"], paths=["/memories/**"], mode="deny"),
    ],
)

# 错误：/workspace/** 命中沙箱 default，抛出 NotImplementedError
# FilesystemPermission(operations=["write"], paths=["/workspace/**"], mode="deny")
```

---

### 4.5 代码沙箱 (Sandboxes)

#### 沙箱到底是什么？

很多初学者会困惑：沙箱是 LangChain 自己的框架？还是第三方服务？答案是**两者都有**。

**一句话定义**：沙箱是 Deep Agents 的一种**后端类型**（Backend），它在隔离的远程环境中为 Agent 提供文件系统工具 + `execute` 工具（运行 shell 命令）。与 StateBackend（内存存储）、FilesystemBackend（本地磁盘）等纯文件后端不同，沙箱后端额外提供了 shell 执行能力和安全隔离边界。

**架构关系**：

```
┌─────────────────────────────────────────────────────┐
│  Deep Agents (框架层)                                 │
│  ┌───────────────────────────────────────────────┐  │
│  │  SandboxBackendProtocol (协议接口)              │  │
│  │  定义了沙箱后端必须实现的方法:                     │  │
│  │  ls / read / write / edit / delete /           │  │
│  │  glob / grep / execute / upload_files / ...    │  │
│  └───────────────────┬───────────────────────────┘  │
│                      │ 实现                           │
│  ┌───────────────────▼───────────────────────────┐  │
│  │  LangChain 集成包 (langchain-xxx)              │  │
│  │  每个供应商一个 pip 包，包装供应商 SDK            │  │
│  │  例: langchain-e2b, langchain-daytona, ...     │  │
│  └───────────────────┬───────────────────────────┘  │
│                      │ 调用                           │
│  ┌───────────────────▼───────────────────────────┐  │
│  │  第三方沙箱服务 (云端)                           │  │
│  │  E2B / Daytona / Modal / Runloop / Vercel /    │  │
│  │  LangSmith Sandbox / AgentCore / ...           │  │
│  │  这些是独立公司提供的隔离计算服务                  │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

关键理解点：

- **Deep Agents 只定义协议**——`SandboxBackendProtocolV2` 是一个 Python 接口（Protocol），规定了沙箱后端必须实现的方法（文件操作 + `execute`）。Deep Agents 本身不提供沙箱基础设施。
- **LangChain 集成包做适配**——每个沙箱供应商有一个对应的 `langchain-xxx` 包（如 `langchain-e2b`），它实现了 `SandboxBackendProtocol`，把供应商的 SDK 包装成 Deep Agents 能用的后端。
- **第三方供应商提供基础设施**——E2B、Daytona、Modal 等是独立公司，提供云端隔离计算环境。你在它们的平台上创建账号，用它们的 SDK 创建沙箱实例。
- **LangSmith Sandbox 是例外**——它是 LangChain 自己的一方服务，无需第三方账号，但有使用限制。
- **你也可以自己实现**——如果你的公司有内部隔离环境，实现 `SandboxBackendProtocol` 接口即可接入。

**沙箱 vs 其他后端的区别**：

| 特性 | StateBackend | FilesystemBackend | Sandbox 后端 |
|------|-------------|-------------------|-------------|
| 文件读写 | 内存中 | 本地磁盘 | 隔离环境中 |
| Shell 执行 | 不支持 | 不支持 | 支持 (`execute` 工具) |
| 安全隔离 | 无需（无副作用） | 无（直接操作主机） | 有（与主机完全隔离） |
| 包安装 | 不支持 | 不支持 | 支持 (`pip install` 等) |
| 典型用途 | 临时中间结果 | 本地开发 CLI | 代码执行 Agent |
| 费用 | 免费 | 免费 | 按使用计费（LangSmith 除外） |

沙箱的核心价值是安全隔离——Agent 可以执行任意代码、安装依赖、运行测试，而不会影响你的主机系统。沙箱消耗资源并产生费用，直到被关闭。

#### 支持的沙箱供应商

| 供应商 | 安装命令 | 说明 |
|--------|---------|------|
| **LangSmith** | `pip install "langsmith[sandbox]"` | LangChain 一方托管沙箱，无需第三方账号 |
| **Daytona** | `pip install langchain-daytona` | 开发环境管理平台 |
| **E2B** | `pip install langchain-e2b` | 云端沙箱 |
| **Modal** | `pip install langchain-modal` | 无服务器计算平台 |
| **Runloop** | `pip install langchain-runloop` | 开发环境平台 |
| **Vercel** | `pip install langchain-vercel-sandbox` | Vercel 沙箱 |
| **AgentCore (AWS)** | 安装对应集成 | AWS AgentCore 代码解释器 |
| **NVIDIA OpenShell** | `pip install langchain-nvidia-openshell` | NVIDIA 策略治理沙箱 |
| **UpstashBox** | 安装对应集成 | Upstash Box 沙箱 |
| **Superserve** | 安装对应集成 | Superserve 沙箱 |
| **Leap0** | 安装对应集成 | Leap0 沙箱 |

#### 示例：LangSmith 沙箱

```python
from deepagents import create_deep_agent
from deepagents.backends.langsmith import LangSmithSandbox
from langsmith.sandbox import SandboxClient

# 创建沙箱客户端和实例
client = SandboxClient()
ls_sandbox = client.create_sandbox()
backend = LangSmithSandbox(sandbox=ls_sandbox)

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    system_prompt="You are a Python coding assistant with sandbox access.",
    backend=backend,
)
try:
    result = agent.invoke({
        "messages": [{"role": "user", "content": "Create a small Python package and run pytest"}]
    })
finally:
    # 沙箱消耗资源，用完必须清理
    client.delete_sandbox(ls_sandbox.name)
```

#### 示例：E2B 沙箱

```python
from e2b import Sandbox
from deepagents import create_deep_agent
from langchain_e2b import E2BSandbox

e2b_sandbox = Sandbox.create()
backend = E2BSandbox(sandbox=e2b_sandbox)

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    system_prompt="You are a Python coding assistant with sandbox access.",
    backend=backend,
)
try:
    result = agent.invoke({
        "messages": [{"role": "user", "content": "Create a small Python package and run pytest"}]
    })
finally:
    e2b_sandbox.kill()
```

#### 沙箱生命周期

沙箱消耗资源并产生费用，直到被关闭。两种常见的作用域：

**线程级（默认）**——每个会话一个沙箱，复用直到线程结束：

```python
from langchain_core.runnables import RunnableConfig
from deepagents import create_deep_agent
from deepagents.backends.langsmith import LangSmithSandbox
from langsmith.sandbox import SandboxClient

client = SandboxClient()

async def agent(config: RunnableConfig):
    thread_id = config["configurable"]["thread_id"]
    sandbox_name = f"thread-{thread_id}"
    existing = [sb for sb in client.list_sandboxes() if getattr(sb, "name", None) == sandbox_name]
    if existing:
        ls_sandbox = existing[0]
    else:
        ls_sandbox = client.create_sandbox(name=sandbox_name, idle_ttl_seconds=3600)
    return create_deep_agent(
        model="ollama:qwen3:8b",
        backend=LangSmithSandbox(sandbox=ls_sandbox),
    )
```

**Assistant 级**——同一 assistant 的所有线程共享一个沙箱：

```python
async def agent(config: RunnableConfig):
    assistant_id = config["configurable"]["assistant_id"]
    sandbox_name = f"assistant-{assistant_id}"
    existing = [sb for sb in client.list_sandboxes() if getattr(sb, "name", None) == sandbox_name]
    if existing:
        ls_sandbox = existing[0]
    else:
        ls_sandbox = client.create_sandbox(name=sandbox_name)
    return create_deep_agent(
        model="ollama:qwen3:8b",
        backend=LangSmithSandbox(sandbox=ls_sandbox),
    )
```

#### 文件传输

```python
# 上传文件到沙箱
backend.upload_files([
    ("/src/index.py", b"print('Hello')\n"),
    ("/pyproject.toml", b"[project]\nname = 'my-app'\n"),
])

# 从沙箱下载文件
results = backend.download_files(["/src/index.py", "/output.txt"])
for result in results:
    if result.content is not None:
        print(f"{result.path}: {result.content.decode()}")
```

#### 推荐集成模式：Sandbox as Tool

Agent 运行在你的服务器上（有 API 密钥），沙箱作为远程执行环境。推荐让密钥留在主机端，不进入沙箱：

```python
agent = create_deep_agent(
    model="ollama:qwen3:8b",
    backend=backend,
    system_prompt="You are a coding assistant with sandbox access.",
)
```

> **安全警告**: 永远不要将密钥放入沙箱。沙箱内的 Agent 可以执行任意命令，如果密钥在沙箱环境中，攻击者可通过上下文注入获取密钥。使用 auth proxy 或在主机端工具中处理认证。

---

### 4.6 解释器 (Interpreters / QuickJS)

解释器提供进程内 JavaScript 运行时（QuickJS），让 Agent 通过编写代码来组合工具、保留状态、进行分支和循环。与沙箱不同，解释器不提供 shell 访问、包安装或文件系统/网络访问——它是一个轻量级的、内存中的可编程层。

> **状态**: Beta（API 可能变化）
> **要求**: `langchain-quickjs>=0.2.0`, Python >=3.11

```bash
pip install -U "deepagents[quickjs]"
```

#### 为什么需要解释器

大多数 Agent 工作在模型推理和工具调用之间交替。模型可以在一轮中发出多个工具调用，但这一批调用在发出时就固定了——不能循环、不能根据结果分支、不能重试失败、不能把一个调用的输出喂给下一个调用（除非再来一轮模型调用），而且每个结果都会进入模型上下文。

解释器把这种编排移到代码中：模型只推理**做什么**，解释器负责**怎么做**。中间结果不进入模型上下文，只有最终结果返回。

#### 基本使用

```python
from deepagents import create_deep_agent
from langchain_quickjs import CodeInterpreterMiddleware

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    middleware=[CodeInterpreterMiddleware()],
)
```

Agent 获得 `eval` 工具，可以编写 JavaScript 代码。代码运行在 QuickJS 上下文中，变量可以在 `eval` 调用间持久化（取决于持久化模式）：

```javascript
// Agent 自动生成的代码示例：数据聚合
const rows = [
  { team: "alpha", score: 8 },
  { team: "beta", score: 13 },
  { team: "alpha", score: 21 },
];

const totals = rows.reduce((acc, row) => {
  acc[row.team] = (acc[row.team] ?? 0) + row.score;
  console.log(`${row.team} score: ${acc[row.team]}`);
  return acc;
}, {});

totals;
```

QuickJS 默认无法访问主机文件系统、网络、shell、包管理器或时钟。只有两个显式的桥接可以扩展能力：PTC（程序化工具调用）和动态子代理。

#### 程序化工具调用 (PTC)

将工具暴露到解释器内的 `tools` 命名空间，Agent 可以在代码中循环/分支/并行调用工具。工具名自动转为 camelCase（如 `web_search` → `tools.webSearch`）：

```python
agent = create_deep_agent(
    model="ollama:qwen3:8b",
    middleware=[CodeInterpreterMiddleware(ptc=["web_search"])],  # 白名单
)
```

```javascript
// Agent 生成的代码：并行搜索多个主题
const topics = ["retrieval", "memory", "evaluation"];
const results = await Promise.all(
  topics.map((topic) => tools.webSearch({ query: `${topic} best practices 2025` })),
);
results.join("\n\n");
```

> **注意**: PTC 调用通过解释器桥接执行，不经过正常的工具调用路径。因此 `interrupt_on` 审批工作流不会对 PTC 调用的工具生效。

#### 动态子代理

解释器中可以通过 `task()` 全局函数派发子代理（当 Agent 配置了子代理时默认启用）：

```javascript
const paths = ["src/auth.ts", "src/routes/api.ts"];
const reviews = await Promise.all(
  paths.map((path) =>
    task({
      description: `Review ${path} for authentication issues`,
      subagentType: "reviewer",
    }),
  ),
);
reviews.join("\n\n");
```

#### 持久化模式

| 模式 | 行为 |
|------|------|
| `"thread"` (默认) | 状态跨 eval 调用和 agent 轮次持久化。每轮结束后快照，下轮恢复 |
| `"turn"` | 状态在一个轮次内跨 eval 持久化，下一轮重置 |
| `"call"` | 每次 eval 都是全新上下文 |

`"thread"` 模式下，快照是解释器内存状态的序列化副本（包括全局变量、函数等）。函数、类等不可序列化的对象在恢复后不可用——访问会抛出错误。快照保留解释器内存，不保留外部副作用（如 PTC 工具调用的副作用）。

跨轮次持久化不需要 checkpointer，但如果需要持久化线程或时间旅行，添加 checkpointer：

```python
from langgraph.checkpoint.memory import MemorySaver

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    checkpointer=MemorySaver(),
    middleware=[CodeInterpreterMiddleware(mode="thread")],
)
```

#### 配置参考

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `memory_limit` | 64 MB | QuickJS 堆内存上限 |
| `timeout` | 5.0 秒 | 每次 eval 超时 |
| `tool_name` | `"eval"` | 暴露给模型的工具名 |
| `capture_console` | `True` | 捕获 console.log/warn/error |
| `max_result_chars` | 4000 | 返回结果截断长度 |
| `ptc` | `None` | PTC 工具白名单 |
| `max_ptc_calls` | 256 | 每次 eval 最大工具调用数 |
| `subagents` | `True` | 是否暴露 `task()` 全局函数 |
| `mode` | `"thread"` | 持久化模式 |

#### 解释器 vs 沙箱：何时用哪个

| 需求 | 使用 |
|------|------|
| 一两个简单的外部调用 | 普通工具调用 |
| 纯内存 JavaScript：循环、分支、重试、数据转换 | 解释器 |
| 从代码中编排大量工具调用（需要 PTC） | 解释器 + PTC |
| 大量独立工作单元、多视角分析 | 解释器 + 动态子代理 |
| Shell 命令、包安装、测试、完整 OS 文件系统 | 沙箱 |

---

### 4.7 任务规划 (Task Planning / TodoList)

通过 `TodoListMiddleware` 提供结构化的任务分解能力，Agent 使用 `write_todos` 工具将复杂任务分解为待办列表。

> **注意**: 自 v0.7 起，任务规划为**可选**（opt-in），需要手动启用。在 v0.7 之前是默认启用的。

```python
from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    middleware=[TodoListMiddleware()],
)
```

**工作原理**：启用后，Agent 获得 `write_todos` 工具。当面对复杂任务时，Agent 会先调用 `write_todos` 创建一个结构化的待办列表，然后逐步执行并更新状态。待办状态为 `'pending'`、`'in_progress'`、`'completed'`。

**触发条件**：
- 任务目标比较复杂，涉及较多步骤
- 用户明确提示要求先规划
- 每完成一个步骤需要修订任务清单

待办列表存储在图状态中，通过 checkpointer 在同一线程的多轮对话间持久化。

---

### 4.8 子代理 (Subagents)

子代理允许主 Agent 将工作委派给专门的子代理。这是解决**上下文膨胀问题**的核心机制——主 Agent 只接收最终结果，而非数十个工具调用的中间输出。

#### 为什么子代理有效

当一个 Agent 调用 `task` 工具委派工作时，子代理在**隔离的上下文窗口**中执行。子代理有自己的消息历史、自己的工具调用、自己的中间结果。当子代理完成后，只有最终文本结果返回给主 Agent 的上下文。这意味着：即使子代理在执行过程中产生了 50,000 token 的工具调用，主 Agent 的上下文只增加了几百 token 的结果摘要。

#### 默认子代理 (`general-purpose`)

Deep Agents 自动添加一个同步的 `general-purpose` 子代理，除非你已经提供了一个同名的。这个子代理默认拥有文件系统工具，可以继承主 Agent 的额外工具。

```python
from deepagents import create_deep_agent

# 自动添加 general-purpose 子代理
agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[internet_search],
)
```

#### 自定义子代理（字典方式）

```python
from deepagents import create_deep_agent

research_subagent = {
    "name": "research-agent",
    "description": "Used to research more in depth questions",
    "system_prompt": "You are a great researcher",
    "tools": [internet_search],
    "model": "ollama:qwen3:8b",  # 可选，默认使用主 Agent 模型
}

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    subagents=[research_subagent],
)
```

#### 子代理完整字段参考

| 字段 | 类型 | 继承行为 | 说明 |
|------|------|---------|------|
| `name` | `str` | — | **必填**。唯一标识符，主 Agent 用此名称调用 `task` 工具 |
| `description` | `str` | — | **必填**。面向行动的描述，主 Agent 据此决定何时委派 |
| `system_prompt` | `str` | 不继承 | **必填**。子代理指令，需包含工具使用和输出格式要求 |
| `tools` | `list[Callable]` | 默认继承；指定则完全覆盖 | 子代理可用工具，保持最小化 |
| `model` | `str \| BaseChatModel` | 默认继承 | 覆盖模型，可为不同子代理使用不同模型 |
| `middleware` | `list[Middleware]` | 不继承 | 额外中间件，`.name` 匹配可替换默认 |
| `interrupt_on` | `dict` | 默认继承；指定则覆盖 | HITL 配置 |
| `skills` | `list[str]` | 不继承（general-purpose 除外） | 技能路径，子代理运行独立的 SkillsMiddleware |
| `response_format` | `ResponseFormat` | — | 结构化输出 schema |
| `permissions` | `list[FilesystemPermission]` | 默认继承；指定则完全替换 | 权限规则 |

#### CompiledSubAgent（预构建 LangGraph 图）

当你需要更复杂的工作流时，可以用 LangChain 的 `create_agent` 或自定义 LangGraph 图作为子代理：

```python
from deepagents import CompiledSubAgent, create_deep_agent
from langchain.agents import create_agent

custom_graph = create_agent(
    model="ollama:qwen3:8b",
    tools=specialized_tools,
    system_prompt="You are a specialized agent for data analysis...",
)

custom_subagent = CompiledSubAgent(
    name="data-analyzer",
    description="Specialized agent for complex data analysis tasks",
    runnable=custom_graph,  # 必须是已 compile() 的图
)

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[internet_search],
    subagents=[custom_subagent],
)
```

如果创建自定义 LangGraph 图，确保图有一个名为 `"messages"` 的状态键。

#### 结构化输出子代理

```python
from pydantic import BaseModel, Field

class ResearchFindings(BaseModel):
    """Structured findings from a research task."""
    summary: str = Field(description="Summary of findings")
    confidence: float = Field(description="Confidence score from 0 to 1")
    sources: list[str] = Field(description="List of source URLs")

research_subagent = {
    "name": "researcher",
    "description": "Researches topics and returns structured findings",
    "system_prompt": "Research the given topic thoroughly. Return your findings.",
    "tools": [web_search],
    "response_format": ResearchFindings,
}

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    subagents=[research_subagent],
)
```

设置 `response_format` 后，父 Agent 收到的是 JSON 而非自由文本。

#### 多专家子代理

```python
subagents = [
    {
        "name": "data-collector",
        "description": "Gathers raw data from various sources",
        "system_prompt": "Collect comprehensive data on the topic",
        "tools": [web_search_tool, api_call, database_query],
    },
    {
        "name": "data-analyzer",
        "description": "Analyzes collected data for insights",
        "system_prompt": "Analyze data and extract key insights",
        "tools": [statistical_analysis],
    },
    {
        "name": "report-writer",
        "description": "Writes polished reports from analysis",
        "system_prompt": "Create professional reports from insights",
        "tools": [format_document],
    },
]

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    system_prompt="You coordinate data analysis and reporting.",
    subagents=subagents,
)
```

主 Agent 可以在单轮中发出多个 `task` 调用，让子代理并行执行。

#### 禁用子代理

要完全移除 `task` 工具和子代理能力，需要两步：

```python
from deepagents import (
    create_deep_agent,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)

# 步骤 1：在 harness profile 中禁用 general-purpose 子代理
register_harness_profile(
    "ollama:qwen3:8b",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
)

# 步骤 2：不传 subagents 参数
agent = create_deep_agent(model="ollama:qwen3:8b")
```

> **注意**: 不要通过 `excluded_middleware` 移除 `SubAgentMiddleware`——它是必需的框架支撑，会抛出 `ValueError`。`general_purpose_subagent.enabled = False` 是官方支持的路径。

#### 子代理最佳实践

- **写清晰的 description**——主 Agent 据此决定何时委派，描述要具体且面向行动
- **保持 system_prompt 详细**——子代理不继承主 Agent 的系统提示，需包含完整的工具使用和输出格式指导
- **最小化工具集**——只给子代理它需要的工具，减少混乱
- **按任务选模型**——简单子代理用便宜模型，复杂推理用强模型
- **要求简洁返回**——在 system_prompt 中明确要求只返回摘要，不返回原始工具输出

#### 子代理调用机制：主 Agent 如何调用子代理？

这是理解 Deep Agents 委派机制的关键。子代理的调用**不是**通过网络协议（如 HTTP、gRPC、A2A），而是通过 **LangGraph 工具调用 + 进程内函数执行**实现的。

**同步子代理的调用流程**：

```
用户: "Research topic X"
    │
    ▼
┌──────────────────────────────────────────────────┐
│ 主 Agent (LangGraph 图)                           │
│                                                    │
│  1. LLM 决定委派 → 生成 tool_call:                 │
│     task(subagent_name="researcher",               │
│          description="Research topic X")            │
│                                                    │
│  2. SubAgentMiddleware 拦截 tool_call              │
│     ├─ 查找名为 "researcher" 的子代理配置           │
│     ├─ 创建新的 LangGraph agent 实例               │
│     │   (用子代理的 model/tools/system_prompt)     │
│     ├─ 创建隔离的上下文 (空的消息历史)              │
│     └─ 同步执行子代理图 (阻塞，等待完成)            │
│                                                    │
│  3. 子代理在隔离上下文中执行                         │
│     ├─ 有自己的 messages 列表                       │
│     ├─ 有自己的工具调用                             │
│     └─ 产生自己的中间结果 (不进入主 Agent 上下文)    │
│                                                    │
│  4. 子代理完成 → 只有最终文本返回                    │
│     └─ 作为 ToolMessage 注入主 Agent 的 messages   │
│                                                    │
│  5. 主 Agent 收到结果摘要，继续推理                  │
└──────────────────────────────────────────────────┘
```

关键要点：

- **调用方式**：主 Agent 的 LLM 生成一个 `task` 工具调用，参数是子代理名称和任务描述。这和调用任何其他工具（如 `read_file`、`web_search`）完全一样——`task` 就是一个普通的 LangGraph 工具。
- **执行方式**：`SubAgentMiddleware` 拦截这个工具调用，在**同一进程内**创建子代理的 LangGraph 图实例并同步执行。没有网络调用、没有序列化/反序列化、没有跨进程通信。
- **隔离方式**：子代理有自己的 `messages` 列表（从空开始），不共享主 Agent 的上下文。子代理执行过程中的所有工具调用和中间结果都留在子代理的上下文中。
- **返回方式**：子代理完成后，只有**最终消息的文本内容**作为 `ToolMessage` 返回给主 Agent。即使子代理执行了 50 次工具调用产生了 100K token 的中间结果，主 Agent 的上下文只增加几百 token。
- **并行调用**：主 Agent 可以在一轮中发出多个 `task` 工具调用，让多个子代理并行执行（但仍然同步阻塞，等所有子代理完成后才继续）。

```python
# 主 Agent 的 LLM 会自动生成这样的工具调用：
# （你不需要手动写这段代码，这是 LLM 的行为）
tool_call = {
    "name": "task",              # SubAgentMiddleware 注册的工具
    "args": {
        "subagent_name": "researcher",    # 子代理的 name 字段
        "description": "Research the latest RAG techniques and summarize key findings",
    }
}

# SubAgentMiddleware 接收到这个调用后：
# 1. 查找 name="researcher" 的子代理配置
# 2. 用该配置创建一个独立的 agent 实例
# 3. 同步执行: subagent.invoke({"messages": [HumanMessage(description)]})
# 4. 返回: ToolMessage(content=subagent的最后一条消息.content)
```

#### 通信协议：不是 A2A，是进程内调用

很多人会问：子代理之间用什么协议通信？是 A2A（Agent-to-Agent）吗？

**同步子代理：无协议，纯进程内函数调用**

同步子代理的通信完全不涉及任何网络协议。它是 Python 进程内的函数调用链：主 Agent 的 LangGraph 图 → `SubAgentMiddleware` → 子代理的 LangGraph 图。就像你在代码里调用一个函数一样，没有 HTTP、没有 RPC、没有消息队列。

**异步子代理：Agent Protocol（不是 A2A）**

异步子代理使用 [Agent Protocol](https://github.com/langchain-ai/agent-protocol)——这是 LangChain 定义的 Agent 通信协议，不是 Google 的 A2A 协议。Agent Protocol 支持两种传输方式：

| 传输方式 | 适用场景 | 机制 |
|---------|---------|------|
| **ASGI 传输**（默认） | 同一部署内 | 进程内 ASGI 函数调用，无网络开销 |
| **HTTP 传输** | 跨部署远程调用 | HTTP 请求到远程 Agent Protocol 服务器 |

**A2A vs Agent Protocol vs 进程内调用**：

| 方面 | 同步子代理 | 异步子代理 (ASGI) | 异步子代理 (HTTP) | A2A 协议 |
|------|-----------|------------------|------------------|---------|
| 通信方式 | 进程内函数调用 | 进程内 ASGI 调用 | HTTP 请求 | HTTP + JSON-RPC |
| 网络开销 | 无 | 无 | 有 | 有 |
| 使用的协议 | 无（Python 函数） | Agent Protocol | Agent Protocol | A2A (Google) |
| Deep Agents 是否使用 | 是（默认） | 是（异步默认） | 是（远程异步） | **否** |
| 执行模型 | 阻塞 | 非阻塞 | 非阻塞 | 取决于实现 |
| 状态管理 | 无状态 | 有状态（独立线程） | 有状态 | 取决于实现 |

> **总结**: Deep Agents 的子代理通信**不使用 A2A 协议**。同步子代理是纯进程内函数调用，异步子代理使用 LangChain 自己的 Agent Protocol。A2A 是 Google 提出的跨 Agent 通信标准，Deep Agents 的架构中不涉及它。A2A 仅在 LangSmith Deployment 对外暴露 Agent 时作为可选协议出现，但那是外部访问接口，不是内部子代理通信。

#### 异步子代理 (Async Subagents)

异步子代理是同步子代理的增强版，让主 Agent（supervisor）可以启动后台任务后立即继续与用户交互，而子代理在后台并发执行。这是 `deepagents>=0.5.0` 的预览功能。

```python
from deepagents import AsyncSubAgent, create_deep_agent

async_subagents = [
    AsyncSubAgent(
        name="researcher",
        description="Research agent for information gathering and synthesis",
        graph_id="researcher",  # langgraph.json 中注册的图 ID
        # 不传 url → ASGI 传输（同进程，推荐默认）
    ),
    AsyncSubAgent(
        name="coder",
        description="Coding agent for code generation and review",
        graph_id="coder",
        url="https://coder-deployment.langsmith.dev",  # 传 url → HTTP 传输（远程）
    ),
]

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    subagents=async_subagents,  # 异步子代理通过 subagents 参数传入
)
```

异步子代理提供 5 个工具（由 `AsyncSubAgentMiddleware` 自动注册）：

| 工具 | 作用 | 返回 |
|------|------|------|
| `start_async_task` | 启动后台任务 | 任务 ID（立即返回） |
| `check_async_task` | 查询任务状态和结果 | 状态 + 结果（如已完成） |
| `update_async_task` | 向运行中的任务发送新指令 | 确认 + 更新后状态 |
| `cancel_async_task` | 取消运行中的任务 | 确认 |
| `list_async_tasks` | 列出所有任务及实时状态 | 全部任务摘要 |

**同步 vs 异步子代理对比**：

| 维度 | 同步子代理 | 异步子代理 |
|------|-----------|-----------|
| 执行模型 | 阻塞——主 Agent 等待子代理完成 | 非阻塞——立即返回任务 ID |
| 并发 | 可并行但阻塞 | 真正并发，主 Agent 可继续交互 |
| 中途更新 | 不支持 | 支持（`update_async_task`） |
| 取消 | 不支持 | 支持（`cancel_async_task`） |
| 状态 | 无状态 | 有状态（独立线程，跨交互保持） |
| 通信协议 | 进程内函数调用 | Agent Protocol（ASGI 或 HTTP） |
| 适合场景 | 需要等待结果再继续 | 长时间运行、可交互式管理 |

> **注意**: 异步子代理需要 LangGraph Deployment（`langgraph up` 或 LangSmith Deployment），因为它依赖 Agent Protocol 服务器来管理子代理线程。同步子代理则可以在任何环境中运行，包括纯本地 `agent.invoke()` 调用。

#### 动态子代理

当配置了 [解释器（Interpreter）](#410-解释器-interpreters--quickjs)时，主 Agent 可以从代码中调度子代理，而不仅限于通过 `task` 工具。这允许在代码中使用循环、分支和条件逻辑来编排子代理：

```python
# 主 Agent 的 LLM 生成 JavaScript 代码来调度子代理：
# for (const topic of topics) {
#     task("researcher", `Research ${topic}`);
# }
# // 所有研究完成后，汇总结果
# task("analyst", "Analyze all research findings");
```

动态子代理通过解释器在沙箱中执行编排代码，比纯工具调用更灵活。详见 [4.10 解释器](#410-解释器-interpreters--quickjs) 和 [动态子代理文档](https://docs.langchain.com/oss/python/deepagents/dynamic-subagents)。

---

### 4.9 技能系统 (Skills)

技能将**领域专业知识**打包为可复用的目录，通过**渐进式披露**按需加载，避免上下文膨胀。技能遵循 [Agent Skills 标准](https://agentskills.io/)，每个技能是一个包含 `SKILL.md` 文件的目录。

#### 渐进式披露：三层加载

这是技能系统区别于记忆系统的核心机制。技能不是一次性全部加载到上下文中，而是分三个层次按需加载：

| 层级 | 加载内容 | 时机 | token 开销 |
|------|---------|------|-----------|
| **1. 元数据** | frontmatter 中的 `name` 和 `description` | Agent 启动时，所有已配置技能 | 极小（每个技能几十 token） |
| **2. 指令** | 完整 SKILL.md 正文 | 当 Agent 判断技能与当前任务相关时 | 按需（可能数千 token） |
| **3. 资源** | `scripts/`、`references/`、`assets/` 下的支持文件 | 调用后按需读取 | 仅使用时 |

这种设计让 Agent 可以"知道"几十个技能的存在（元数据开销极小），但只在需要时才加载详细指令，避免上下文被大量不相关的领域知识占满。

#### SKILL.md 结构

```markdown
---
name: langgraph-docs
description: Use this skill for requests related to LangGraph in order to fetch relevant documentation.
license: MIT
compatibility: Requires internet access
metadata:
  author: langchain
  version: "1.0"
allowed-tools: fetch_url
---

# langgraph-docs

## Overview

This skill explains how to access LangGraph documentation.

## Instructions

### 1. Fetch the documentation index
Use the fetch_url tool to read: https://docs.langchain.com/llms.txt

### 2. Select relevant documentation
Based on the question, identify 2-4 most relevant documentation URLs.
```

#### 创建技能

```
skills/
  langgraph-docs/
    SKILL.md          ← 技能入口（必需）
    scripts/          ← 可执行脚本（可选）
    references/       ← 参考文档（可选）
    assets/           ← 静态资源（可选）
```

#### 使用技能

```python
from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend

backend = FilesystemBackend(root_dir="./my-project")

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    backend=backend,
    skills=["./my-project/skills/"],
)

result = agent.invoke(
    {"messages": [{"role": "user", "content": "What is LangGraph?"}]},
    config={"configurable": {"thread_id": "1"}},
)
```

#### StateBackend 加载远程技能

使用 StateBackend 时，可以通过 `create_file_data` 在调用时注入技能文件：

```python
from urllib.request import urlopen
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.backends.utils import create_file_data
from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()
backend = StateBackend()

skill_url = "https://raw.githubusercontent.com/langchain-ai/deepagents/refs/heads/main/libs/cli/examples/skills/langgraph-docs/SKILL.md"
with urlopen(skill_url) as response:
    skill_content = response.read().decode('utf-8')

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    backend=backend,
    skills=["/skills/"],
    checkpointer=checkpointer,
)

result = agent.invoke(
    {
        "messages": [{"role": "user", "content": "What is langgraph?"}],
        "files": {"/skills/langgraph-docs/SKILL.md": create_file_data(skill_content)},
    },
    config={"configurable": {"thread_id": "12345"}},
)
```

#### 技能 vs 记忆 vs 工具

| 方面 | Skills | Memory | Tools |
|------|--------|--------|-------|
| **目的** | 按需加载的能力 | 持久化上下文 | 可编程的动作 |
| **加载时机** | Agent 判断相关性时 | Agent 启动时 | 每轮可用 |
| **格式** | 目录中的 `SKILL.md` | `AGENTS.md` 文件 | 绑定到 Agent 的函数 |
| **token 开销** | 渐进式，按需 | 始终占用上下文 | 仅工具 schema |

---

### 4.10 长期记忆 (Memory)

通过 `AGENTS.md` 文件提供**始终加载**的持久化上下文——项目约定、用户偏好、关键准则。与技能不同，记忆文件在 Agent 启动时就全部注入系统提示，没有渐进式披露。

```python
from deepagents import create_deep_agent

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    memory=["/project/AGENTS.md", "~/.deepagents/preferences.md"],
)
```

记忆的内容存储在配置的后端中（StateBackend、StoreBackend 或 FilesystemBackend）。Agent 也可以根据交互和反馈更新记忆，使偏好和模式在无需每次重述的情况下延续。

#### 跨会话记忆（CompositeBackend + StoreBackend）

```python
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langgraph.store.memory import InMemoryStore

store = InMemoryStore()

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    store=store,
    backend=CompositeBackend(
        default=StateBackend(),
        routes={
            "/memories/": StoreBackend(namespace=lambda _rt: ("memories",)),
        },
    ),
    system_prompt="""When users tell you their preferences, save them to
    /memories/user_preferences.txt so you remember them in future conversations.""",
)
```

#### 只读记忆（保护共享知识库）

```python
from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend

agent = create_deep_agent(
    model=model,
    backend=CompositeBackend(
        default=StateBackend(),
        routes={
            "/memories/": StoreBackend(namespace=lambda rt: (rt.server_info.user.identity,)),
            "/policies/": StoreBackend(namespace=lambda rt: (rt.context.org_id,)),
        },
    ),
    permissions=[
        FilesystemPermission(
            operations=["write"],
            paths=["/memories/**", "/policies/**"],
            mode="deny",
        ),
    ],
)
```

#### 记忆作用域

| 作用域 | 命名空间 | 用途 |
|--------|---------|------|
| **用户级**（推荐默认） | `(user_id)` | 每用户偏好和上下文 |
| **Assistant 级** | `(assistant_id)` | 同一 assistant 的共享指令 |
| **组织级** | `(org_id)` | 所有用户/assistant 的只读策略 |

> **最佳实践**: 保持记忆文件精简——始终加载的内容会占用每轮的上下文。将详细的工作流和领域内容放到技能中（按需加载），只把始终相关的约定放到记忆中。

---

### 4.11 上下文工程 (Context Engineering)

每个 `create_deep_agent` 调用都内置上下文压缩机制，无需额外配置。这是 Deep Agents 能处理长周期任务的关键——自动管理上下文使其在 token 限制内持续运作。

#### 自动卸载 (Offloading)

当会话上下文达到模型窗口的 **85%** 时，自动触发卸载：

1. **工具调用输入超过 20,000 tokens** → 截断较旧的工具调用，替换为文件路径指针
2. **工具调用结果超过 20,000 tokens** → 结果转存到后端文件（`/large_tool_results/` 目录），上下文中替换为文件路径引用 + 前 10 行预览

卸载是对单条消息的操作——它不删除消息，而是把消息中的大块内容替换为引用。Agent 需要时可以通过 `read_file` 分页读取完整内容。

#### 自动摘要 (Summarization)

当上下文接近模型窗口限制且无可卸载内容时，`SummarizationMiddleware` 自动触发：

1. LLM 生成结构化摘要（会话意图、创建的产物、下一步）替换完整历史
2. 原始对话消息写入文件系统作为归档记录

**参数：**
- 触发阈值：模型 `max_input_tokens` 的 85%
- 保留近期上下文：约 10% 的 tokens
- 回退（模型未报告 token 数时）：170,000-token 触发 / 保留 6 条消息

#### 按需压缩工具

除了自动压缩，你还可以提供一个让 Agent 主动调用的压缩工具：

```python
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.summarization import create_summarization_tool_middleware

backend = StateBackend()
model = "ollama:qwen3:8b"

agent = create_deep_agent(
    model=model,
    middleware=[
        create_summarization_tool_middleware(model, backend),
    ],
)
```

这会注入一个 `compact_conversation` 工具，Agent 可以在认为必要时主动压缩上下文。

#### 自定义摘要触发

```python
from deepagents.middleware import SummarizationMiddleware

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    backend=StateBackend(),
    middleware=[
        SummarizationMiddleware(
            model="ollama:qwen3:8b",
            backend=StateBackend(),
            trigger=("tokens", 100000),  # 超过 100k tokens 时摘要
            keep=("messages", 20),        # 保留最近 20 条消息
        ),
    ],
)
```

当你通过 `middleware=` 传入一个 `.name` 与默认 `SummarizationMiddleware` 匹配的实例时，它会原地替换默认实例。

#### 提示缓存

Anthropic 和 Bedrock 模型自动启用提示缓存（默认 5 分钟 TTL），无需配置。静态提示部分（系统提示、工具定义）被标记为可缓存，加速推理并降低成本。可自定义 TTL：

```python
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    middleware=[
        AnthropicPromptCachingMiddleware(ttl="1h"),
    ],
)
```

#### 完整的系统提示组装

Agent 的系统消息由以下部分按顺序组装：

1. 自定义 `system_prompt`（如果提供）
2. 内置基础 Agent 提示
3. Memory 提示：`AGENTS.md` 内容 + 记忆使用指南（仅当 `memory` 提供时）
4. Skills 提示：技能位置 + 技能列表（含 frontmatter）+ 使用说明（仅当 `skills` 提供时）
5. 虚拟文件系统提示（文件系统工具 + execute 工具文档）
6. 子代理提示（task 工具使用指导）
7. 用户中间件提示（如果有自定义中间件）
8. 人机交互提示（当 `interrupt_on` 设置时）

#### 上下文隔离（子代理）

子代理通过隔离重工作来保持主 Agent 上下文清洁。关键是子代理的 `system_prompt` 中要明确要求简洁返回：

```python
research_subagent = {
    "name": "researcher",
    "description": "Conducts research on a topic",
    "system_prompt": """You are a research assistant.
    IMPORTANT: Return only the essential summary (under 500 words).
    Do NOT include raw search results or detailed tool outputs.""",
    "tools": [web_search],
}
```

---

### 4.12 人机交互 (Human-in-the-Loop)

通过 `interrupt_on` 参数配置需要人工审批的工具，Agent 在执行前暂停等待审批。这是让 Agent 安全执行高风险操作的关键机制。

> **要求**: 必须配置 checkpointer（中断需要持久化状态以便恢复）

#### 基本配置

```python
from langchain.tools import tool
from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver

@tool
def remove_file(path: str) -> str:
    """Delete a file from the filesystem."""
    return f"Deleted {path}"

@tool
def notify_email(to: str, subject: str, body: str) -> str:
    """Send an email."""
    return f"Sent email to {to}"

# checkpointer 是必需的
checkpointer = MemorySaver()

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[remove_file, notify_email],
    interrupt_on={
        "remove_file": True,                                            # 默认：approve/edit/reject/respond
        "notify_email": {"allowed_decisions": ["approve", "reject"]},   # 不允许编辑
    },
    checkpointer=checkpointer,
)
```

`interrupt_on` 接受三种值：
- **`True`**：启用中断，默认允许所有决策类型
- **`False`**：禁用中断
- **`InterruptOnConfig`**：自定义配置，可设置 `allowed_decisions` 和 `when` 谓词

#### 决策类型

| 决策 | 说明 | 适用场景 |
|------|------|---------|
| `approve` | 按原始参数执行工具 | 批准邮件草稿原样发送 |
| `edit` | 修改参数后执行 | 修改收件人后发送邮件 |
| `reject` | 跳过执行，返回拒绝反馈给 Agent | 拒绝文件删除并说明原因 |
| `respond` | 直接返回人类消息作为工具结果（不执行工具） | 回答 `ask_user` 类工具的提问 |

> **注意**: `respond` 用于人类充当工具的场景（如回答问题），不要用它来拒绝有副作用的工具——其消息可能被模型当作成功的工具结果。

#### 条件中断（when 谓词）

> **要求**: `langchain>=1.3.3`

不是所有工具调用都需要审批——通过 `when` 谓词可以只在特定条件下暂停：

```python
from langchain.agents.middleware import ToolCallRequest

def writes_outside_workspace(request: ToolCallRequest) -> bool:
    """仅在工作区外写入时暂停。"""
    path = request.tool_call["args"].get("file_path", "")
    return not path.startswith("/workspace/")

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    interrupt_on={
        "write_file": {
            "allowed_decisions": ["approve", "edit", "reject"],
            "when": writes_outside_workspace,
        },
    },
    checkpointer=MemorySaver(),
)
```

`when` 返回 `False` 时自动放行，返回 `True` 时暂停。只有需要决策的调用才会进入中断批次。

#### 处理中断与恢复

```python
from langchain_core.utils.uuid import uuid7
from langgraph.types import Command

# 每个会话用唯一的 thread_id
config = {"configurable": {"thread_id": str(uuid7())}}

# 第一次调用 — 可能被中断
result = agent.invoke(
    {"messages": [{"role": "user", "content": "Delete the file temp.txt"}]},
    config=config,
    version="v2",
)

# 检查是否被中断
if result.interrupts:
    interrupt_value = result.interrupts[0].value
    action_requests = interrupt_value["action_requests"]

    # 提供决策（顺序与 action_requests 一致）
    decisions = [
        {"type": "reject", "message": "User rejected. Do not retry deletion."}
    ]

    # 恢复执行（必须使用相同的 config！）
    result = agent.invoke(
        Command(resume={"decisions": decisions}),
        config=config,
        version="v2",
    )

print(result.value["messages"][-1].content)
```

> **关键**: 恢复时必须使用与中断时相同的 `config`（相同的 `thread_id`），因为中断状态存储在检查点中。

#### 编辑工具参数

```python
if result.interrupts:
    action_request = result.interrupts[0].value["action_requests"][0]

    decisions = [{
        "type": "edit",
        "edited_action": {
            "name": action_request["name"],
            "args": {"to": "team@company.com", "subject": "...", "body": "..."}
        }
    }]

    result = agent.invoke(
        Command(resume={"decisions": decisions}),
        config=config,
        version="v2",
    )
```

> **注意**: 编辑参数时保守修改。大幅修改可能导致模型重新评估其方法，多次执行工具或采取意外行动。

#### 子代理中断

子代理可以有自己的 `interrupt_on` 配置，与主 Agent 不同：

```python
agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[delete_file, read_file],
    interrupt_on={
        "delete_file": True,
        "read_file": False,
    },
    subagents=[{
        "name": "file-manager",
        "description": "Manages file operations",
        "system_prompt": "You are a file management assistant.",
        "tools": [delete_file, read_file],
        "interrupt_on": {
            "delete_file": True,
            "read_file": True,  # 与主 Agent 不同！
        }
    }],
    checkpointer=checkpointer
)
```

#### 恢复模式汇总

| 中断类型 | 恢复格式 |
|---------|---------|
| 工具调用中断 | `Command(resume={"decisions": [...]})` |
| 多工具调用 | `Command(resume={"decisions": [..., ...]})` |
| 编辑参数 | `Command(resume={"decisions": [{"type": "edit", "edited_action": {...}}]})` |
| 拒绝 | `Command(resume={"decisions": [{"type": "reject", "message": "..."}]})` |
| 工具内直接 `interrupt()` | `Command(resume={"approved": True})` |
| 文件系统权限中断 | `Command(resume={"decisions": [{"type": "approve"}]})` |

#### 最佳实践

- **始终使用 checkpointer**——HITL 依赖检查点持久化中断状态
- **使用相同的 thread_id**——恢复时必须与中断时使用相同的线程
- **决策顺序与操作顺序一致**——多个工具调用同时被中断时，decisions 列表顺序要对应
- **按风险分级配置**——高风险操作允许所有决策，中风险不允许编辑，低风险不中断

```python
interrupt_on = {
    # 高风险：完全控制
    "delete_file": {"allowed_decisions": ["approve", "edit", "reject"]},
    "send_email": {"allowed_decisions": ["approve", "edit", "reject"]},
    # 中风险：不允许编辑
    "write_file": {"allowed_decisions": ["approve", "reject"]},
    # 低风险：不中断
    "read_file": False,
    "ls": False,
}
```

---

### 4.13 事件流式输出 (Event Streaming)

Deep Agents 在 LangGraph 流式输出基础上增加了子代理流投影。流式输出让你能实时跟踪 Agent 的每一步——消息生成、工具调用、子代理委派——而非等待最终结果。

#### 流式子代理

```python
stream = agent.stream_events(
    {"messages": [{"role": "user", "content": "Write me a haiku about the sea"}]},
    version="v3",
)

subagent_names = []
for subagent in stream.subagents:
    print(subagent.name, subagent.path, subagent.status)
    for message in subagent.messages:
        print(message.text)
    subagent_names.append(subagent.name)
```

`stream.subagents` 是一个投影——它发现每个 `task` 调用并为其提供一个独立的流句柄。每个句柄的 `name` 是子代理的配置名称（即主 Agent 调用 `task` 时传入的 `subagent_type`）。

#### 子代理流字段

| 字段 | 说明 |
|------|------|
| `name` | 子代理名称（来自 `subagent_type`） |
| `messages` | 子代理发出的消息 |
| `subagents` | 嵌套子代理调用 |
| `output` | 最终状态或完成信号 |
| `path` | 命名空间路径 |
| `status` | 生命周期状态：`started`/`completed`/`failed`/`interrupted` |
| `tool_calls` | 子代理的工具调用 |

#### 流式消息

```python
stream = agent.stream_events(input, version="v3")

for message in stream.messages:
    print("[coordinator]", message.text)

for subagent in stream.subagents:
    for message in subagent.messages:
        print(f"[{subagent.name}]", message.text)
```

#### 流式工具调用

```python
stream = agent.stream_events(input, version="v3")

for call in stream.tool_calls:
    print("[coordinator tool]", call.tool_name, call.input)

for subagent in stream.subagents:
    for call in subagent.tool_calls:
        print(f"[{subagent.name} tool]", call.tool_name, call.input)
        for delta in call.output_deltas:
            print(delta, end="", flush=True)
```

#### 并发消费

主 Agent 和子代理的输出经常交错。需要实时 UI 更新时，应并发消费：

```python
# 同步：使用 interleave
stream = agent.stream_events(input, version="v3")

for name, item in stream.interleave("messages", "subagents"):
    if name == "messages":
        print("[coordinator]", item.text)
    else:
        for message in item.messages:
            print(f"[{item.name}]", message.text)
```

```python
# 异步：使用 asyncio.gather
stream = await agent.astream_events(input, version="v3")

async def consume_coordinator():
    async for message in stream.messages:
        print("[coordinator]", await message.text)

async def consume_subagents():
    async for subagent in stream.subagents:
        async for message in subagent.messages:
            print(f"[{subagent.name}]", await message.text)

await asyncio.gather(consume_coordinator(), consume_subagents())
```

#### 流式嵌套子代理

```python
stream = agent.stream_events(input, version="v3")

for subagent in stream.subagents:
    print(f"subagent {subagent.name}: {subagent.status}")
    for tool_call in subagent.tool_calls:
        print(f"  {tool_call.tool_name}({tool_call.input})")
    for nested in subagent.subagents:
        print(f"  nested: {nested.name}: {nested.status}")
```

#### subagents vs subgraphs

`stream.subgraphs` 显示图执行结构（LangGraph 节点级别）。`stream.subagents` 显示产品级别的 Deep Agents 任务委派。用户界面应使用 `stream.subagents`，因为它隐藏内部图节点，直接暴露子代理概念。

---

## 5. 其他能力

### 5.1 结构化输出 (Structured Output)

```python
from pydantic import BaseModel, Field

class WeatherReport(BaseModel):
    """A structured weather report."""
    location: str = Field(description="The location")
    temperature: float = Field(description="Temperature in Celsius")
    condition: str = Field(description="Weather condition")
    forecast: str = Field(description="Brief forecast")

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    response_format=WeatherReport,
    tools=[internet_search],
)

result = agent.invoke(
    {"messages": [{"role": "user", "content": "What's the weather in SF?"}]}
)
print(result["structured_response"])
# location='San Francisco' temperature=18.3 condition='Sunny' forecast='...'
```

`response_format` 接受 Pydantic 模型、`ToolStrategy(...)`、`ProviderStrategy(...)` 或原始 schema 类型。结果通过 `result["structured_response"]` 获取。

### 5.2 Harness Profiles（模型配置档案）

按模型自动应用的配置包——当行为应跟随**模型**而非调用点时使用。Profile 在 `create_deep_agent` 检测到匹配的模型字符串时自动激活。

```python
from deepagents import HarnessProfile, register_harness_profile

# 每当选择 qwen3:8b 时自动追加系统提示后缀
register_harness_profile(
    "ollama:qwen3:8b",
    HarnessProfile(system_prompt_suffix="Respond in under 100 words."),
)
```

```python
from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)

# 对所有 Anthropic 模型生效
register_harness_profile(
    "anthropic",
    HarnessProfile(
        base_system_prompt="You are ACME's support orchestrator.",
        general_purpose_subagent=GeneralPurposeSubagentProfile(
            system_prompt="You are a research subagent. Cite sources.",
        ),
        system_prompt_suffix="Always think step by step.",
    ),
)
```

Profile 的能力包括：
- `base_system_prompt`：替换基础提示
- `system_prompt_suffix`：追加到系统提示末尾
- `excluded_tools`：隐藏特定工具
- `general_purpose_subagent`：配置默认子代理
- `tool_description_overrides`：覆盖工具描述

### 5.3 多模态支持 (Multimodal)

`read_file` 工具原生返回多模态内容块（见 [4.3 多模态文件支持](#多模态文件支持)）。自定义工具也可返回多模态内容块——在工具返回值中包含文本 + 图片/音频/视频/文件内容块即可。

### 5.4 运行时上下文 (Runtime Context)

每次调用时传入的配置，自动传播到所有子代理。用于传递用户身份、API 密钥、特性开关等运行时数据。运行时上下文不会自动出现在模型提示中——只有工具或中间件读取它时才生效。

```python
from dataclasses import dataclass
from deepagents import create_deep_agent
from langchain.tools import ToolRuntime, tool

@dataclass
class Context:
    user_id: str
    api_key: str

@tool
def fetch_user_data(query: str, runtime: ToolRuntime[Context]) -> str:
    """Fetch data for the current user."""
    user_id = runtime.context.user_id
    return f"Data for user {user_id}: {query}"

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[fetch_user_data],
    context_schema=Context,
)

result = agent.invoke(
    {"messages": [{"role": "user", "content": "Get my recent activity"}]},
    context=Context(user_id="user-123", api_key="sk-..."),
)
```

工具通过注入的 `ToolRuntime` 对象访问上下文。`runtime.context` 是你定义的 Context 实例，`runtime.store` 可以访问 LangGraph Store。

### 5.5 自定义状态 (Custom State Schema)

> **要求**: `deepagents>=0.6.6`

当你需要在图状态中存储自定义数据（除了 messages 和 files）时，扩展 `DeepAgentState`：

```python
from deepagents import DeepAgentState, create_deep_agent
from langchain.tools import ToolRuntime, tool

class ResearchState(DeepAgentState):
    page_url: str
    file_urls: list[str]

@tool
def cite_page(runtime: ToolRuntime) -> str:
    """Return the current page URL."""
    return runtime.state["page_url"]

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[cite_page],
    state_schema=ResearchState,
)

result = agent.invoke(
    {
        "messages": [{"role": "user", "content": "Cite the current page"}],
        "page_url": "https://example.com/report",
        "file_urls": [],
    },
)
```

工具通过 `runtime.state` 访问自定义状态字段。状态在图执行期间持久化，通过 checkpointer 跨轮次保存。

---

## 6. 中间件架构详解

### 中间件是什么

中间件是 Deep Agents 架构的扩展机制。每个中间件是一个类，实现 `AgentMiddleware` 接口，可以在 Agent 执行生命周期的不同阶段注入逻辑：

- `before_agent` / `abefore_agent`：Agent 执行开始前（注入工具、修改系统提示）
- `before_model` / `abefore_model`：每次模型调用前（修改消息、工具列表）
- `after_model` / `aafter_model`：每次模型调用后（处理响应）
- `wrap_tool_call` / `awrap_tool_call`：拦截工具调用（日志、审计、修改参数）

Deep Agents 的所有内置能力（文件系统、子代理、摘要、HITL 等）都是通过中间件实现的。你也可以编写自定义中间件来扩展行为。

### 默认中间件栈

主 Agent 的默认中间件栈（按执行顺序）：

| 顺序 | 中间件 | 作用 | 条件 |
|------|--------|------|------|
| 1 | SkillsMiddleware | 注入技能工具和提示 | 仅当传入 skills |
| 2 | FilesystemMiddleware | 文件系统工具 + 权限 | 始终（核心，不可移除） |
| 3 | SubAgentMiddleware | task 工具 + 子代理委派 | 仅当有同步子代理 |
| 4 | SummarizationMiddleware | 上下文压缩 | 始终 |
| 5 | PatchToolCallsMiddleware | 修复中断后的悬空工具调用 | 始终 |
| 6 | AsyncSubAgentMiddleware | 异步子代理 | 仅当有异步子代理 |
| 7 | 用户自定义 middleware | 自定义逻辑 | 用户传入 |
| 8 | Harness Profile extras | 模型特定中间件 | 按 profile 配置 |
| 9 | 工具过滤 | 移除 excluded_tools | 按 profile 配置 |
| 10 | 提示缓存中间件 | Anthropic/Bedrock 缓存 | 仅对应模型 |
| 11 | MemoryMiddleware | 加载 AGENTS.md | 仅当传入 memory |
| 12 | HumanInTheLoopMiddleware | 工具调用审批 | 仅当传入 interrupt_on |

### 自定义中间件

```python
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import tool
from deepagents import create_deep_agent

@tool
def get_weather(city: str) -> str:
    """Get the weather in a city."""
    return f"The weather in {city} is sunny."

class WeatherMiddleware(AgentMiddleware):
    tools = [get_weather]

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    middleware=[WeatherMiddleware()],
)
```

### 工具调用拦截中间件

```python
from langchain.agents.middleware import wrap_tool_call

call_count = [0]

@wrap_tool_call
def log_tool_calls(request, handler):
    """拦截并记录每次工具调用。"""
    call_count[0] += 1
    tool_name = request.name if hasattr(request, "name") else str(request)
    print(f"[Middleware] Tool call #{call_count[0]}: {tool_name}")
    result = handler(request)
    print(f"[Middleware] Tool call #{call_count[0]} completed")
    return result

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[get_weather],
    middleware=[log_tool_calls],
)
```

### 覆盖默认中间件

> **要求**: `deepagents>=0.7`，通过 `.name` 匹配替换

当你传入的中间件实例的 `.name` 属性与某个默认中间件匹配时，它会**原地替换**默认实例：

```python
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware import SummarizationMiddleware

backend = StateBackend()
model = "ollama:qwen3:8b"

agent = create_deep_agent(
    model=model,
    middleware=[
        SummarizationMiddleware(
            model=model,
            backend=backend,
            trigger=("tokens", 100000),
            keep=("messages", 20),
        ),
    ],
)
```

### 限制文件系统工具

> **要求**: `deepagents>=0.7`

通过传入自定义的 `FilesystemMiddleware` 实例，只暴露部分文件系统工具：

```python
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware import FilesystemMiddleware

backend = StateBackend()

# 只读 Agent：write_file/edit_file/delete/execute 不会显示给模型
agent = create_deep_agent(
    model="ollama:qwen3:8b",
    backend=backend,
    middleware=[
        FilesystemMiddleware(backend=backend, tools=["read_file", "ls", "glob", "grep"]),
    ],
)
```

> **注意**: `read_file` 必须始终包含在工具白名单中——省略会抛出 `ValueError`。`execute` 和 `delete` 在后端不支持时也会自动移除，无论是否包含在白名单中。自定义工具不受此白名单影响。

这个限制会传递给 `general-purpose` 子代理，但不会传递给声明式子代理——需要在子代理的 `middleware` 字段中单独配置。

### PII 中间件（数据隐私）

```python
from langchain.agents.middleware import PIIMiddleware

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    middleware=[
        PIIMiddleware("email", strategy="redact", apply_to_input=True),
        PIIMiddleware("credit_card", strategy="mask", apply_to_input=True),
    ],
)
```

**策略**: `redact`（替换为 `[REDACTED_EMAIL]`）、`mask`（部分遮蔽）、`hash`（确定性哈希）、`block`（报错）。

---

## 7. 推荐部署方案

### 两种推荐路径

| 方案 | 说明 | 适用场景 |
|------|------|---------|
| **Managed Deep Agents** | LangSmith 中的 CLI 优先托管运行时（私有预览） | 想要最简路径，无需自定义基础设施 |
| **LangSmith Deployment** | 传统部署，自定义应用代码和路由 | 需要自定义代码、路由或高级认证 |

**两者都自动提供**: 线程、运行、Store 和 Checkpointer。

**LangSmith Deployment 额外提供**: 认证、Webhooks、Cron 作业、可观测性、MCP/A2A 协议暴露。

### 配置文件 (langgraph.json)

```json
{
  "dependencies": ["."],
  "graphs": {
    "agent": "./agent.py:agent"
  },
  "env": ".env"
}
```

### 本地开发

```bash
# 使用 langgraph dev 启动开发服务器
langgraph dev
```

### 自托管部署

如果你不想使用 LangSmith 托管平台，可以用以下两种方式自托管 Deep Agent。适合需要数据驻留、自定义认证或私有化部署的场景。

> **注意**: LangServe（`langserve` 包）已于 2024 年 11 月正式废弃 [$TRAE_REF](https://github.com/langchain-ai/langserve)，官方不再接受新功能贡献，推荐迁移到 LangGraph Platform。以下方案不使用 LangServe。

#### 方案一：LangGraph Platform 自托管（官方推荐）

LangGraph CLI 提供了完整的自托管工作流：`langgraph dev` 本地开发 → `langgraph build` 构建 Docker 镜像 → `langgraph up` 启动完整服务栈（API + Postgres + Redis）。这种方式与 LangSmith 托管使用同一套 API 接口，迁移成本为零。

```bash
# 1. 安装 LangGraph CLI
pip install langgraph-cli

# 2. 本地开发（热重载，无需 Docker）
langgraph dev

# 3. 构建 Docker 镜像（用于生产部署）
langgraph build -t my-deep-agent:latest

# 4. 启动完整服务栈（API + Postgres + Redis）
langgraph up
```

你只需要一个 `langgraph.json` 配置文件（见上文）和一个导出 `agent` 变量的 Python 文件：

```python
# agent.py — 导出 Deep Agent 实例
from deepagents import create_deep_agent

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[],
    system_prompt="You are a helpful assistant.",
)
```

启动后自动暴露标准 REST API，客户端用 LangGraph SDK 调用：

```python
from langgraph_sdk import get_client
import asyncio

async def main():
    client = get_client(url="http://localhost:2024")

    # 创建线程（会话）
    thread = await client.threads.create()

    # 发送消息
    result = await client.runs.wait(
        thread_id=thread["thread_id"],
        assistant_id="agent",
        input={"messages": [{"role": "user", "content": "Hello!"}]},
    )
    print(result)

asyncio.run(main())
```

LangGraph Platform 自托管内置了：Postgres 持久化检查点、Redis 任务队列、流式 SSE 端点、线程管理、HITL 中断/恢复 API——无需手动实现这些基础设施。

#### 方案二：纯 FastAPI 自定义端点

当你需要完全自定义请求/响应格式、集成现有认证系统或嵌入已有 API 时，可以不用 LangGraph Platform，直接用 FastAPI 包装 Deep Agent：

```bash
pip install fastapi uvicorn
```

```python
"""
app.py — 纯 FastAPI 自托管 Deep Agent（不依赖 LangServe）
启动: uvicorn app:app --host 0.0.0.0 --port 8000 --workers 2
"""
import uuid
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from langgraph.checkpoint.postgres import PostgresSaver
from deepagents import create_deep_agent
import psycopg

# ─── Agent 创建 ───
# 生产级 checkpointer（不要用 MemorySaver！）
# MemorySaver 是内存存储，服务重启后状态丢失，且不支持多实例水平扩展
conn = psycopg.connect("postgresql://user:password@localhost/langgraph")
checkpointer = PostgresSaver(conn=conn)
checkpointer.setup()  # 首次运行时创建表

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[],
    system_prompt="You are a helpful assistant.",
    checkpointer=checkpointer,
)

app = FastAPI(title="Deep Agent API")

# ─── 请求/响应模型 ───
class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None  # 首次对话不传，后续复用

class ChatResponse(BaseModel):
    reply: str
    thread_id: str

# ─── 简单 API Key 认证 ───
async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != "your-secret-key":
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key

# ─── 聊天端点 ───
@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _=Depends(verify_api_key)):
    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    result = agent.invoke(
        {"messages": [{"role": "user", "content": req.message}]},
        config=config,
    )

    return ChatResponse(
        reply=result["messages"][-1].content,
        thread_id=thread_id,
    )

# ─── 流式端点（SSE）───
@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, _=Depends(verify_api_key)):
    from fastapi.responses import StreamingResponse
    import json

    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    async def event_stream():
        async for event in agent.astream_events(
            {"messages": [{"role": "user", "content": req.message}]},
            config=config,
            version="v2",
        ):
            if event["event"] == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if chunk.content:
                    yield f"data: {json.dumps({'content': chunk.content, 'thread_id': thread_id})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

客户端调用：

```bash
# 调用聊天端点
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-key" \
  -d '{"message": "Hello!", "thread_id": "session-1"}'

# 流式调用
curl -N -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-key" \
  -d '{"message": "Write a poem about cats"}'
```

#### 两种方案对比

| 方面 | LangGraph Platform 自托管 | 纯 FastAPI |
|------|--------------------------|-----------|
| 基础设施 | 内置 Postgres + Redis + API | 需自己搭建 |
| HITL 支持 | 内置中断/恢复 API | 需手动实现 |
| 流式输出 | 内置 SSE | 需手动实现 |
| 线程管理 | 内置 | 需手动管理 thread_id |
| 认证 | 支持自定义认证 | 完全自定义 |
| 灵活性 | 标准 API，扩展受限 | 完全自由 |
| 适合场景 | 大多数生产部署 | 嵌入现有系统 |

#### 生产部署检查清单

| 事项 | 开发环境 | 生产环境 |
|------|---------|---------|
| Checkpointer | `MemorySaver()`（内存，重启丢失） | `PostgresSaver()`（持久化，支持多实例） |
| Thread ID | 任意字符串 | UUID 或 `hash(user_id)`，避免碰撞 |
| 认证 | 无 | API Key / OAuth / JWT |
| 进程管理 | `uvicorn app:app` | `gunicorn -k uvicorn.workers.UvicornWorker -w 4` |
| HTTPS | 无 | Nginx/Caddy 反向代理 + TLS |
| 递归限制 | 默认 25 | 根据任务复杂度调高（如 100-10000） |
| 超时 | 无 | 设置 `recursion_limit` + 请求超时 |
| 监控 | print | LangSmith / Langfuse（见下文） |

> **关键提醒**: `MemorySaver` 是进程内 RAM，服务重启后所有对话状态消失。多实例部署时，负载均衡器会将同一用户的不同请求路由到不同实例，导致状态不一致。生产环境**必须**使用 `PostgresSaver` 或 `SqliteSaver` 等共享存储后端。

### 生产部署调用示例

```python
from dataclasses import dataclass
from deepagents import create_deep_agent
from langchain_core.utils.uuid import uuid7

@dataclass
class Context:
    user_id: str

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    context_schema=Context,
)

# 开始新会话——每个会话用唯一的 thread_id
config = {"configurable": {"thread_id": str(uuid7())}}
agent.invoke(
    {"messages": [{"role": "user", "content": "Plan a 3-day trip to Tokyo"}]},
    config=config,
    context=Context(user_id="user-123"),
)

# 后续消息：复用相同的 thread_id 续接对话
agent.invoke(
    {"messages": [{"role": "user", "content": "Make it 5 days instead"}]},
    config=config,
    context=Context(user_id="user-123"),
)
```

### LangGraph SDK 调用

```python
from langgraph_sdk import get_client

client = get_client(url="<DEPLOYMENT_URL>", api_key="<LANGSMITH_API_KEY>")

thread = await client.threads.create()
async for chunk in client.runs.stream(
    thread["thread_id"],
    "agent",
    input={"messages": [{"role": "user", "content": "Plan a trip to Tokyo"}]},
    context={"user_id": "user-123"},
    stream_mode="updates",
):
    print(chunk.data)
```

### 前端连接

```javascript
import { useStream } from "@langchain/react";

function App() {
  const stream = useStream({
    apiUrl: "https://your-deployment.langsmith.dev",
    assistantId: "agent",
  });

  // 多子代理工作流需要高递归限制
  stream.submit(
    { messages: [{ type: "human", content: text }] },
    {
      streamSubgraphs: true,
      config: { recursionLimit: 10000 },
    },
  );
}
```

### 可观测性与监控

Agent 在生产环境中的行为是黑盒——你看不到它为什么做了某个决策、为什么调用了某个工具、为什么花了这么长时间。可观测性工具把黑盒变成白盒。

#### 方案一：LangSmith（LangChain 一方服务）

[LangSmith](https://smith.langchain.com) 是 LangChain 官方的追踪、评估和监控平台，与 Deep Agents 深度集成。

```bash
pip install langsmith
```

```python
import os

# 设置 LangSmith（设置环境变量即可自动启用追踪）
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_API_KEY"] = "ls-..."
os.environ["LANGSMITH_PROJECT"] = "my-deep-agent"

from deepagents import create_deep_agent

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[],
    system_prompt="You are a helpful assistant.",
)

# 设置环境变量后，所有 invoke/astream 调用自动被追踪
result = agent.invoke(
    {"messages": [{"role": "user", "content": "Hello!"}]},
    config={"configurable": {"thread_id": "session-1"}},
)
```

LangSmith 提供：
- **请求追踪**——完整的调用链可视化：模型调用 → 工具执行 → 子代理委派 → 中断恢复
- **Agent 行为调试**——查看每一步的输入/输出、token 消耗、延迟
- **输出评估**——定义评估器对 Agent 输出打分，支持人工标注和自动评估
- **LangSmith Engine**——自动监控生产 traces，检测异常（如无限循环、工具调用失败激增）并提议修复

> **提示**: LangSmith 的沙箱集成还能在 traces 中查看 Agent 在沙箱内执行了哪些 shell 命令、如何使用文件系统工具。

#### 方案二：Langfuse（开源，可自托管）

[Langfuse](https://langfuse.com) 是开源的 AI 工程平台，提供与 LangSmith 类似的追踪和监控能力，但**支持自托管**，适合对数据隐私有严格要求的团队。Langfuse 官方提供了 [Deep Agents 专用集成](https://langfuse.com/integrations/frameworks/langchain-deepagents)。

```bash
pip install langfuse deepagents
```

```python
import os

# 方式 A：使用 Langfuse Cloud
os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-lf-..."
os.environ["LANGFUSE_SECRET_KEY"] = "sk-lf-..."
os.environ["LANGFUSE_BASE_URL"] = "https://cloud.langfuse.com"

# 方式 B：自托管 Langfuse（Docker 部署）
# os.environ["LANGFUSE_BASE_URL"] = "http://localhost:3000"

from langfuse import get_client
from langfuse.langchain import CallbackHandler
from deepagents import create_deep_agent

# 初始化 Langfuse 追踪处理器
langfuse = get_client()
langfuse_handler = CallbackHandler()

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[],
    system_prompt="You are a helpful assistant.",
)

# 通过 callbacks 参数注入追踪
result = agent.invoke(
    {"messages": [{"role": "user", "content": "What is LangGraph?"}]},
    config={"callbacks": [langfuse_handler]},
)
```

Langfuse 追踪会自动捕获：
- Agent 的规划和推理步骤
- 工具调用（含输入/输出和耗时）
- 子代理交互（嵌套追踪）
- Token 使用量和延迟
- 完整对话历史

你还可以用 `@observe()` 装饰器添加自定义属性（用户 ID、会话 ID、标签等）：

```python
from langfuse import observe, propagate_attributes

@observe()
def run_agent(user_input: str, user_id: str):
    with propagate_attributes(
        user_id=user_id,
        session_id="session-abc",
        tags=["production", "deep-agent"],
        version="1.0.0",
    ):
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            config={"callbacks": [langfuse_handler]},
        )
        return result["messages"][-1].content
```

#### LangSmith vs Langfuse 对比

| 方面 | LangSmith | Langfuse |
|------|-----------|----------|
| 开源 | 否（闭源 SaaS） | 是（MIT 开源） |
| 自托管 | 企业版支持 | 免费自托管（Docker） |
| Deep Agents 集成 | 原生（环境变量自动启用） | Callback Handler 注入 |
| 沙箱追踪 | 支持（查看 shell 命令） | 支持（通用追踪） |
| 自动评估 | LangSmith Engine 自动检测 | 支持自定义评估器 |
| 数据隐私 | 数据存储在 LangSmith 云端 | 自托管时数据完全在本地 |
| 适用场景 | LangChain 生态深度用户 | 需要数据驻留/私有化部署 |

> **选择建议**: 如果你的团队已经在使用 LangChain 生态且无特殊数据隐私要求，LangSmith 是最简路径。如果你需要自托管、数据本地化，或者使用多框架混合技术栈，Langfuse 更灵活。两者可以同时使用——Langfuse 的 OpenTelemetry 支持让它能与 LangSmith 并存。

### 持久化执行 (Durability)

基于 LangGraph 的检查点机制：
- **无限期中断** — HITL 工作流可暂停数分钟或数天
- **时间旅行** — 回退到任意检查点并从早期状态重放
- **安全处理敏感操作** — 审计跟踪和恢复点

### 多租户安全

- **用户认证** — 自定义认证建立用户身份
- **授权处理器** — 控制资源访问，标记所有权元数据
- **OAuth Agent Auth** — 托管 OAuth 2.0 流程，令牌自动存储和刷新
- **密钥管理** — Workspace secrets 存储共享密钥；沙箱使用 auth proxy

### 端到端综合示例：多代理研究助手

以下示例将本文档涵盖的多个核心能力整合到一个可运行的应用中。它演示了：自定义工具、子代理委派、CompositeBackend 混合存储、权限控制、HITL 审批、长期记忆、结构化输出、运行时上下文。

```python
"""
端到端综合示例：多代理研究助手
功能：接收用户问题 → 规划任务 → 委派子代理研究 → 审批后写入报告 → 返回结构化结果
依赖：pip install deepagents "langchain[google-genai]"
"""

import os
from dataclasses import dataclass
from pydantic import BaseModel, Field

from deepagents import (
    create_deep_agent,
    DeepAgentState,
    FilesystemPermission,
)
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from langchain_core.utils.uuid import uuid7
from langchain.tools import tool, ToolRuntime

os.environ["GOOGLE_API_KEY"] = "..."

# ─── 1. 运行时上下文：传递用户身份 ───

@dataclass
class AppContext:
    user_id: str
    org_id: str

# ─── 2. 自定义工具 ───

@tool
def web_search(query: str) -> str:
    """Search the web for information."""
    # 实际实现接入搜索 API
    return f"Search results for: {query}"

@tool
def save_report(title: str, content: str, runtime: ToolRuntime) -> str:
    """Save a research report to the user's workspace.

    Args:
        title: Report title
        content: Full report content in markdown
    """
    user_id = runtime.context.user_id
    # 通过文件系统工具写入（权限控制在 /workspace/ 下）
    return f"Report '{title}' saved for user {user_id}"

# ─── 3. 结构化输出 ───

class ResearchReport(BaseModel):
    """Structured research report."""
    topic: str = Field(description="Research topic")
    summary: str = Field(description="Executive summary (under 200 words)")
    key_findings: list[str] = Field(description="Key findings")
    sources: list[str] = Field(description="Source URLs")
    confidence: float = Field(description="Confidence score 0-1")

# ─── 4. 子代理：研究员（隔离上下文） ───

researcher_subagent = {
    "name": "researcher",
    "description": "Researches a topic using web search and returns findings",
    "system_prompt": """You are a research assistant.
    IMPORTANT: Return only a concise summary (under 300 words).
    Do NOT include raw search results.""",
    "tools": [web_search],
    "permissions": [
        # 研究员只读，不能写文件
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
        FilesystemPermission(operations=["read"], paths=["/**"], mode="allow"),
    ],
}

# ─── 5. 构建 Agent ───

store = InMemoryStore()
checkpointer = InMemorySaver()

agent = create_deep_agent(
    model="ollama:qwen3:8b",
    tools=[save_report],
    subagents=[researcher_subagent],
    system_prompt="""You are a research coordinator.
    1. Delegate research tasks to the 'researcher' subagent.
    2. Synthesize findings into a coherent report.
    3. Use 'save_report' to save the final report.
    Always cite sources and rate your confidence.""",
    # 混合后端：内部数据临时，记忆持久
    backend=CompositeBackend(
        default=StateBackend(),
        routes={
            "/memories/": StoreBackend(
                namespace=lambda rt: (rt.server_info.user.identity,)
            ),
        },
    ),
    store=store,
    checkpointer=checkpointer,
    # HITL：保存报告前需人工审批
    interrupt_on={
        "save_report": {"allowed_decisions": ["approve", "edit", "reject"]},
    },
    # 权限：只允许在 /workspace/ 下写操作
    permissions=[
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/workspace/**"],
            mode="allow",
        ),
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ],
    # 结构化输出
    response_format=ResearchReport,
    # 运行时上下文
    context_schema=AppContext,
    # 长期记忆：加载用户偏好
    memory=["/memories/preferences.md"],
)

# ─── 6. 运行 ───

config = {"configurable": {"thread_id": str(uuid7())}}
context = AppContext(user_id="user-123", org_id="acme")

# 第一轮：提交研究请求
result = agent.invoke(
    {"messages": [{"role": "user", "content": "Research the latest advances in RAG and write a summary report."}]},
    config=config,
    context=context,
)

# 检查是否因 save_report 被中断
if result.interrupts:
    print("Agent 请求审批保存报告...")
    # 人工审批
    result = agent.invoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config=config,
        context=context,
    )

# 获取结构化结果
report: ResearchReport = result["structured_response"]
print(f"主题: {report.topic}")
print(f"摘要: {report.summary}")
print(f"发现: {len(report.key_findings)} 条")
print(f"置信度: {report.confidence:.0%}")

# 后续对话：复用相同 thread_id 续接
result2 = agent.invoke(
    {"messages": [{"role": "user", "content": "What were the top 3 findings?"}]},
    config=config,
    context=context,
)
```

这个示例展示了 Deep Agents 的完整工作流：用户输入 → Agent 委派子代理研究（隔离上下文）→ 综合结果 → HITL 审批保存 → 返回结构化报告。注意几个关键设计决策：

- **子代理只读权限**——研究员只需要搜索和读取，不需要写入，通过权限最小化降低风险
- **CompositeBackend 混合存储**——内部临时数据用 StateBackend，用户记忆用 StoreBackend 跨会话持久化
- **HITL 在 save_report 上**——报告写入是有副作用的操作，需要人工确认
- **结构化输出**——最终返回 `ResearchReport` 对象而非自由文本，便于下游处理
- **运行时上下文**——用户身份通过 `AppContext` 传播，工具和 StoreBackend 命名空间都能使用

---

## 8. 安全模型与最佳实践

### Deep Agents 安全模型

> **"Trust the LLM" 模型** — Agent 可以做其工具允许的任何事情。在工具/沙箱层面强制执行边界，而非期望模型自我约束。

这意味着：不要依赖提示词来阻止 Agent 做危险操作。如果一个工具能删除文件，Agent 就能删除文件——无论你在系统提示里写什么"不要删除文件"的指令。正确的做法是：通过权限控制限制文件系统访问范围，通过 HITL 审批敏感操作，通过沙箱隔离代码执行。

### 文件系统安全

| 后端 | 安全性 | 建议 |
|------|--------|------|
| StateBackend | 安全 | 默认，适合生产 |
| StoreBackend | 安全 | 跨线程持久化 |
| FilesystemBackend | 危险 | 仅用于本地开发/CI |
| LocalShellBackend | 极度危险 | 仅用于可信本地环境 |
| Sandbox | 安全 | 生产环境推荐 |

### 沙箱安全

**沙箱保护的：**
- Agent 读取本地文件
- Agent 访问主机环境变量
- Agent 干扰主机进程

**沙箱不保护的：**
- 上下文注入 — 攻击者控制 Agent 输入可在沙箱内运行任意命令
- 网络泄露 — 除非阻止网络，Agent 可通过 HTTP/DNS 发送数据

**密钥处理原则：**
1. **永远不要将密钥放入沙箱**
2. 推荐方案 1：密钥留在主机端工具中
3. 推荐方案 2：使用 auth proxy 自动注入认证头
4. 如果必须注入：启用所有工具调用的 HITL 审批 + 阻止网络 + 最小权限 + 最短生命周期

### 权限最佳实践

```python
# 按风险分级配置
interrupt_on = {
    # 高风险：完全控制
    "delete_file": {"allowed_decisions": ["approve", "edit", "reject"]},
    "send_email": {"allowed_decisions": ["approve", "edit", "reject"]},
    # 中风险：不允许编辑
    "write_file": {"allowed_decisions": ["approve", "reject"]},
    # 低风险：不中断
    "read_file": False,
    "ls": False,
}
```

### 异步最佳实践

- 创建异步工具（避免同步工具的线程开销）
- 使用异步中间件方法（`abefore_agent` 而非 `before_agent`）
- 沙箱创建和 MCP 服务器连接涉及网络调用，应使用 `await`

---

## 9. 选型对比：何时使用 Deep Agents

### LangChain 生态三层选择

| 框架 | 定位 | 何时使用 |
|------|------|---------|
| **LangGraph** | 底层运行时 | 需要完全控制工作流、自定义节点和边 |
| **LangChain (`create_agent`)** | 高层 Agent 接口 | 简单工具调用 Agent，不需要文件系统/子代理 |
| **Deep Agents (`create_deep_agent`)** | 深度 Agent 框架 | 复杂多步骤长周期任务，需要规划/文件系统/子代理/记忆 |

这三层是组合关系：任何 LangGraph `CompiledStateGraph` 都可以作为子代理传入 Deep Agent。你可以用 LangGraph 构建自定义编排，然后作为 `CompiledSubAgent` 嵌入 Deep Agent 中，与框架的默认能力并存。

### 使用 Deep Agents 的信号

- 任务涉及多步骤、长时间运行（数十次工具调用）
- 需要任务规划和待办跟踪
- 需要文件系统作为上下文缓冲区
- 需要子代理进行任务分工和上下文隔离
- 需要跨会话的长期记忆
- 需要人机交互审批
- 需要沙箱代码执行

### 不使用 Deep Agents 的信号

- 简单单步任务 → 用 `create_agent`
- 需要完全自定义工作流 → 用 LangGraph
- 不需要文件系统/子代理/规划 → 用 `create_agent`

### Deep Agents vs Claude Agent SDK

| 方面 | Deep Agents | Claude Agent SDK |
|------|-------------|------------------|
| 开源 | MIT | 闭源 |
| 模型 | 供应商无关 | Anthropic 专用 |
| 运行时 | LangGraph | 自有 |
| 扩展性 | 中间件架构 | 有限 |

---

## 附录：完整参数参考

### `create_deep_agent` 完整签名

```python
create_deep_agent(
    model: str | BaseChatModel | None = None,
    tools: Sequence[BaseTool | Callable | dict[str, Any]] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    middleware: Sequence[AgentMiddleware] = (),
    subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    permissions: list[FilesystemPermission] | None = None,
    backend: BackendProtocol | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    response_format: ResponseFormat | type | dict | None = None,
    state_schema: type[DeepAgentState] | None = None,
    context_schema: type | None = None,
    checkpointer: Checkpointer | None = None,
    store: BaseStore | None = None,
    debug: bool = False,
    name: str | None = None,
    cache: BaseCache | None = None,
) -> CompiledStateGraph
```

### 参数说明

| 参数 | 作用 |
|------|------|
| `model` | 使用的模型（字符串或模型对象） |
| `tools` | 领域工具列表（普通函数或 @tool 装饰器） |
| `system_prompt` | 自定义系统提示（前置到内置提示之前） |
| `middleware` | 额外中间件（`.name` 匹配可替换内置） |
| `subagents` | 自定义子代理列表（字典或 CompiledSubAgent） |
| `skills` | 技能目录路径列表 |
| `memory` | AGENTS.md 文件路径列表 |
| `permissions` | 文件系统权限规则列表 |
| `backend` | 文件系统后端（默认 StateBackend） |
| `interrupt_on` | HITL 配置 |
| `response_format` | 结构化输出 schema |
| `state_schema` | 自定义图状态 schema（扩展 DeepAgentState） |
| `context_schema` | 运行时上下文 schema |
| `checkpointer` | 检查点器（HITL/记忆必需） |
| `store` | LangGraph Store（跨线程持久化） |
| `debug` | 调试模式 |
| `name` | Agent 名称 |
| `cache` | 缓存 |

### 内置工具完整列表

| 工具 | 说明 | 来源 | 最低版本 |
|------|------|------|---------|
| `ls` | 列出目录文件 | FilesystemMiddleware | — |
| `read_file` | 读取文件（支持分页、多模态） | FilesystemMiddleware | — |
| `write_file` | 创建/覆盖文件 | FilesystemMiddleware | — |
| `edit_file` | 精确字符串替换 | FilesystemMiddleware | — |
| `delete` | 递归删除 | FilesystemMiddleware | >=0.7 |
| `glob` | 按模式查找文件 | FilesystemMiddleware | — |
| `grep` | 搜索文件内容 | FilesystemMiddleware | — |
| `execute` | 运行 shell 命令 | 沙箱后端 | — |
| `task` | 派生子代理 | SubAgentMiddleware | — |
| `write_todos` | 任务规划 | TodoListMiddleware（opt-in） | >=0.7 (opt-in) |
| `eval` | JavaScript 执行 | CodeInterpreterMiddleware（opt-in） | — |
| `compact_conversation` | 按需上下文压缩 | SummarizationToolMiddleware（opt-in） | — |

### 版本要求参考

| 功能 | 最低版本 |
|------|---------|
| `delete` 工具 | >=0.7 |
| `tools` 白名单 on FilesystemMiddleware | >=0.7 |
| 任务规划 opt-in（非默认） | >=0.7 |
| 权限功能 | >=0.5.2 |
| `interrupt` 权限模式 | >=0.6.8 |
| 自定义状态 schema | >=0.6.6 |
| 结构化输出子代理 | >=0.5.3 |
| Namespace Runtime 直接传递 | >=0.5.2 |
| `delete` 精确匹配权限行为 | >=0.7.3 |
| 条件中断 `when` 谓词 | langchain>=1.3.3 |
| 解释器 (QuickJS) | langchain-quickjs>=0.2.0, Python>=3.11 |

---

> **文档版本**: 基于截至 2026 年 8 月的官方文档和 GitHub 仓库（v0.7.5）整理校验
> **官方文档**: https://docs.langchain.com/oss/python/deepagents/overview
> **GitHub**: https://github.com/langchain-ai/deepagents
> **API 参考**: https://reference.langchain.com/python/deepagents
