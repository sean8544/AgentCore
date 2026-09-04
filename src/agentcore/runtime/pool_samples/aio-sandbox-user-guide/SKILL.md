---
name: aio-sandbox-user-guide
description: AIO Sandbox 用户指南：当用户需要了解或使用 AIO Sandbox 的 MCP（Model Context Protocol）能力——查看/配置 MCP 服务器、连接与探测 MCP 端点、调用沙箱或浏览器 MCP 工具、排查 MCP 连接问题时使用。
---

# AIO Sandbox 用户指南（MCP 用法）

## 何时使用

- 用户询问「AIO Sandbox 怎么用 MCP」「如何连接/配置 MCP 服务器」「MCP 端点是什么」
- 用户提到 `mcp.json`、`mcp-hub.json`、`python-server`、`mcp-server-browser`、浏览器 MCP 工具
- 需要探测某个 MCP 端点是否可用、列出/调用工具、或排查连接失败
- 需要在沙箱里新增一个 MCP 服务器并接入现有端点

## 架构速览

AIO Sandbox 的 MCP 采用「一个集线端点 + 多个后端 MCP 服务器」的结构：

```
客户端 (Claude Desktop / 任何 MCP client)
   │  streamable HTTP
   ▼
http://<host>:8080/mcp  ──nginx──►  http://127.0.0.1:8091/mcp   (python-server，集线端点)
   │                                        │ 聚合转发
   │                                        ▼
   │                        /opt/gem/mcp-hub.json 里注册的 MCP 服务器
   │                                        │
   │                                        ▼
   │                        http://127.0.0.1:8100/mcp  (mcp-server-browser)
   │                                        │ CDP
   │                                        ▼
   │                                        Chrome (127.0.0.1:9222)
```

- **集线端点**：`python-server`（FastAPI + FastMCP，进程名 `Sandbox MCP Tools`），
  启动参数 `--mcp-config /opt/gem/mcp-hub.json --filter-mcp-servers sandbox`。
  它会按集线配置把多个后端 MCP 服务器的工具聚合到同一个 `/mcp` 端点上。
- **后端 MCP 服务器**：当前内置一个浏览器 MCP 服务器 `mcp-server-browser`
  （进程名 `Web Browser`），通过 CDP 连接沙箱内的 Chrome。
- **客户端配置**：`~/mcp.json` 是给外部 MCP 客户端使用的服务器清单（stdio 或 HTTP）。

## 关键路径与端点

| 项目 | 值 |
|---|---|
| 集线端点（本机） | `http://127.0.0.1:8091/mcp` |
| 集线端点（经 nginx，对外） | `http://<host>:8080/mcp`、`http://<host>:8080/v1/mcp` |
| 浏览器 MCP 端点 | `http://127.0.0.1:8100/mcp` |
| Chrome 调试端口 | `http://127.0.0.1:9222`（CDP） |
| 健康检查 | `curl http://127.0.0.1:8091/health` → `{"status":"healthy"}` |
| 集线配置 | `/opt/gem/mcp-hub.json` |
| 客户端配置 | `~/mcp.json`（即 `/home/<user>/mcp.json`） |
| 日志目录 | `/var/log/gem/`（`python-server.log`、`mcp-server-browser.log`） |

## 核心工作流

### 1. 查看当前 MCP 配置

```bash
cat /opt/gem/mcp-hub.json      # 集线配置：哪些后端服务器被聚合
cat ~/mcp.json                 # 客户端配置：给外部客户端声明的服务器
```

### 2. 探测 MCP 端点（是否存活、有什么工具）

streamable HTTP 传输需要同时带 `Content-Type` 与 `Accept` 头：

```bash
# 握手（initialize）
curl -s -X POST http://127.0.0.1:8091/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-03-26","capabilities":{},
                 "clientInfo":{"name":"probe","version":"1.0"}}}'

# 列出工具（集线端点会返回聚合后的全部工具）
curl -s -X POST http://127.0.0.1:8091/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | jq -r '.result.tools[].name'

# 直接探测浏览器 MCP（跳过集线）
curl -s -X POST http://127.0.0.1:8100/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | jq -r '.result.tools[].name'
```

### 3. 调用 MCP 工具

```bash
curl -s -X POST http://127.0.0.1:8091/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call",
       "params":{"name":"browser_navigate",
                 "arguments":{"url":"https://example.com"}}}'
```

### 4. 配置新的 MCP 服务器

- **集线配置（后端聚合）**：编辑 `/opt/gem/mcp-hub.json`，在 `mcpServers` 下加条目：

  ```json
  {
    "mcpServers": {
      "browser": { "type": "streamable-http", "url": "http://127.0.0.1:8100/mcp" },
      "my-server": { "type": "streamable-http", "url": "http://127.0.0.1:9000/mcp" }
    }
  }
  ```

  集线端点懒加载：首次请求时才读取配置并连接后端（见日志
  `Lazy initializing MCP server`）。修改后重启 `python-server` 可强制重新加载。

- **客户端配置（对外声明）**：编辑 `~/mcp.json`，字段为
  `server_id`、`name`、`transport`（`stdio` 或 `streamable_http`）、`command`（stdio 用）、
  `url`（HTTP 用）、`env`、`headers`、`enabled`：

  ```json
  {
    "playwright": {
      "server_id": "playwright", "name": "Playwright 浏览器",
      "transport": "stdio", "command": "npx -y @playwright/mcp@latest",
      "url": "", "env": {}, "headers": {}, "enabled": true
    },
    "my-http-server": {
      "server_id": "my-http-server", "name": "My Server",
      "transport": "streamable_http", "command": "",
      "url": "http://localhost:9000/mcp", "env": {}, "headers": {}, "enabled": true
    }
  }
  ```

### 5. 浏览器 MCP 典型流程

1. `browser_navigate` 打开目标 URL
2. `browser_get_markdown` / `browser_get_text` / `browser_read_links` 读取页面内容
3. 需要交互时 `browser_get_clickable_elements` 拿到可点元素 → `browser_click` / `browser_type` / `browser_select_option`
4. 截图取证用 `browser_screenshot`（单标签页）或 `browser_gui_screenshot`（整个显示）
5. 完成 `browser_close`

### 6. 故障排查 checklist

按顺序执行，遇错修错：

1. 端口是否在监听：`ss -tlnp | grep -E '8091|8100|9222'`
2. 集线是否健康：`curl -s http://127.0.0.1:8091/health`
3. 浏览器 MCP 是否响应：对 `8100/mcp` 发 `initialize`（见工作流 2）
4. 配置是否生效：`cat /opt/gem/mcp-hub.json`，确认 `url` 指向的端口确实有服务
5. 查日志：`tail -n 100 /var/log/gem/python-server.log`、`/var/log/gem/mcp-server-browser.log`
6. 用 supervisor 看进程状态：`supervisorctl status`
7. 客户端 `~/mcp.json` 里 `enabled` 是否为 `true`、`transport` 与 `url`/`command` 是否配对

## 注意事项

- **头必须带全**：streamable HTTP 端点若不带 `Accept: application/json, text/event-stream` 会返回 406（本环境 `/mcp` 无头访问即 406）。
- **懒加载**：集线端点第一次请求才初始化后端连接，首次 `tools/list` 会稍慢；日志见 `Lazy initializing MCP server`。
- **`--filter-mcp-servers sandbox`**：集线只加载配置里被过滤允许的服务器，新增服务器后确认过滤条件不拦它。
- **配置了但没进程**：`~/mcp.json` 里的 `aio-mcp` 若指向 `http://localhost:47492/mcp` 而该端口无人监听，说明该服务器未启动；要么启动对应服务，要么从配置里禁用。
- **端口绑定**：`8091`（集线）、`8100`（浏览器 MCP）、`9222`（Chrome CDP）默认只绑定 `127.0.0.1`，对外访问走 nginx 8080。
- **不臆测工具名**：工具清单以 `tools/list` 实时返回为准，版本升级可能增删工具。

## 参考

- 工具清单、配置字段详解、进程/环境变量、日志位置 → 见 `reference.md`
