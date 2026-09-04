# AIO Sandbox MCP 详细参考

本文件是 `SKILL.md` 的详细参考，按主题拆分，供深入排查与扩展使用。

## 1. 端点速查

| 端点 | 说明 | 绑定 |
|---|---|---|
| `http://127.0.0.1:8091/mcp` | 集线端点（python-server，聚合所有后端 MCP 工具） | 127.0.0.1:8091 |
| `http://127.0.0.1:8091/health` | 健康检查，返回 `{"status":"healthy"}` | 同上 |
| `http://127.0.0.1:8100/mcp` | 浏览器 MCP 服务器（Web Browser） | 127.0.0.1:8100 |
| `http://127.0.0.1:9222` | Chrome CDP 调试端口（`/json/version`） | 127.0.0.1:9222 |
| `http://<host>:8080/mcp` | nginx 对外入口 → 转发到 8091 | 0.0.0.0:8080 |
| `http://<host>:8080/v1/mcp` | nginx 别名入口 → 转发到 8091/mcp | 0.0.0.0:8080 |

其他占用端口（供参考）：8080(nginx)、8091(python-server)、8092(node REPL)、8100(mcp-server-browser)、8200、8888(jupyter-lab)、9222(chrome)、5900/6080(VNC/websocat)。

## 2. 集线端点（python-server）工具清单

通过 `tools/list` 返回（当前 32 个，版本升级可能变化），分为两组：

### 沙箱工具（sandbox_*）
- `sandbox_get_context` — 沙箱环境信息（版本、home 目录）
- `sandbox_execute_bash` — 执行 shell 命令
- `sandbox_execute_code` — 执行 Python/JavaScript 代码
- `sandbox_file_operations` — 统一文件操作（read/write/replace/search/find/list/grep/glob）
- `sandbox_str_replace_editor` — 文本编辑器（str_replace 风格）
- `sandbox_get_packages` — 已安装包列表
- `sandbox_load_skill` — 加载技能（SKILL.md）
- `sandbox_convert_to_markdown` — 把 http/https/file/data URI 转成 markdown

### 浏览器 GUI 工具（本机显示）
- `browser_get_info` — 浏览器信息（CDP url、viewport 等）
- `browser_gui_screenshot` — 全显示截图（含所有标签页）
- `browser_gui_execute_action` — 在显示上执行鼠标/键盘/滚轮等动作

### 浏览器 MCP 工具（经 mcp-server-browser 聚合）
见第 4 节完整清单。资源：`console://logs`（浏览器控制台日志）。

## 3. 配置参考

### 3.1 集线配置 `/opt/gem/mcp-hub.json`

```json
{
  "mcpServers": {
    "browser": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8100/mcp"
    }
  }
}
```

- `mcpServers`：map，key 是服务器名，value 是连接配置。
- `type`：当前用 `streamable-http`（也兼容 `sse` 等类型）。
- `url`：后端 MCP 端点地址。
- 由 `python-server` 启动参数 `--mcp-config /opt/gem/mcp-hub.json --filter-mcp-servers sandbox` 加载。

### 3.2 客户端配置 `~/mcp.json`

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `server_id` | string | 唯一标识 |
| `name` | string | 显示名称 |
| `transport` | string | `stdio`（本地进程）或 `streamable_http`（远程端点） |
| `command` | string | stdio 传输时要执行的命令（如 `npx -y @playwright/mcp@latest`） |
| `url` | string | HTTP 传输时的端点地址（如 `http://localhost:47492/mcp`） |
| `env` | object | 传给进程的环境变量（stdio 用） |
| `headers` | object | HTTP 请求头（HTTP 用） |
| `enabled` | bool | 是否启用 |

示例（内置的 playwright + 一个 HTTP 服务器）：

```json
{
  "playwright": {
    "server_id": "playwright",
    "name": "Playwright 浏览器",
    "transport": "stdio",
    "command": "npx -y @playwright/mcp@latest",
    "url": "",
    "env": {},
    "headers": {},
    "enabled": true
  },
  "aio-mcp": {
    "server_id": "aio-mcp",
    "name": "aio-mcp",
    "transport": "streamable_http",
    "command": "",
    "url": "http://localhost:47492/mcp",
    "env": {},
    "headers": {},
    "enabled": true
  }
}
```

注意：`aio-mcp` 指向的 47492 端口若无人监听，该服务器不可用，需要先启动对应服务。

## 4. 浏览器 MCP（mcp-server-browser）工具清单

启动方式（supervisor 配置见下）：连接本机 Chrome 的 CDP 端点，viewport `0,0`。

工具（以 `tools/list` 实时返回为准）：

| 工具 | 用途 |
|---|---|
| `browser_navigate` | 导航到 URL |
| `browser_go_back` / `browser_go_forward` | 前进/后退 |
| `browser_get_markdown` | 当前页 markdown 内容 |
| `browser_get_text` | 当前页纯文本 |
| `browser_read_links` | 页面所有链接 |
| `browser_new_tab` / `browser_tab_list` / `browser_switch_tab` / `browser_close_tab` | 标签页管理 |
| `browser_get_clickable_elements` | 可点击元素索引 |
| `browser_click` / `browser_hover` / `browser_select` / `browser_scroll` | 交互 |
| `browser_form_input_fill` / `browser_press_key` / `browser_type` | 表单与键盘 |
| `browser_evaluate` | 在页面执行 JS |
| `browser_screenshot` | 截图（单标签页） |
| `browser_get_download_list` | 下载列表 |
| `browser_close` | 关闭浏览器 |

资源：`console://logs`（浏览器控制台日志）。

CLI 常用参数（`mcp-server-browser --help`）：

- `--browser <chrome|edge|firefox>` — 浏览器渠道
- `--cdp-endpoint <url>` — 连接现有 Chrome，如 `http://127.0.0.1:9222/json/version`
- `--ws-endpoint <url>` — 或直接给 WebSocket 端点
- `--executable-path <path>` / `--headless` / `--user-data-dir <path>`
- `--host` / `--port` — 监听地址端口
- `--output-dir <path>` — 输出文件目录
- `--proxy-server` / `--proxy-bypass` / `--user-agent`
- `--viewport-size "1280,720"` — 视口大小
- `--vision` — 使用截图式 Aria 快照

## 5. 进程与服务管理

### 5.1 进程

| 进程 | 命令（要点） | 端口 |
|---|---|---|
| python-server | `/usr/local/bin/python-server --host 127.0.0.1 --port 8091 --mcp-config /opt/gem/mcp-hub.json --filter-mcp-servers sandbox --workspace /home/gem` | 8091 |
| mcp-server-browser | `mcp-server-browser --port 8100 --host 127.0.0.1 --browser chrome --cdp-endpoint http://127.0.0.1:9222/json/version` | 8100 |
| chrome | 远程调试模式 | 9222 |

### 5.2 supervisor 管理

配置目录：`/opt/gem/supervisord/`，相关文件：

- `supervisord.python_srv.conf` — python-server
- `supervisord.mcp.conf` — mcp-server-browser
- `agent-browser.conf` — 浏览器启动初始化

常用命令：

```bash
supervisorctl status
supervisorctl restart python-server
supervisorctl restart mcp-server-browser
```

环境变量（supervisor 配置中引用，如 `ENV_SANDBOX_SRV_PORT`、`ENV_MCP_SERVER_BROWSER_PORT`、`ENV_BROWSER_REMOTE_DEBUGGING_PORT`、`ENV_WORKSPACE`、`ENV_LOG_DIR`）。

### 5.3 日志

- `/var/log/gem/python-server.log` — 集线端点日志（含 MCP 懒加载/连接记录）
- `/var/log/gem/mcp-server-browser.log` — 浏览器 MCP 日志
- `/var/log/gem/nginx-access.log` / `nginx-error.log` — 反向代理访问日志
- 关键日志关键词：`Lazy initializing MCP server`、`Loading MCP servers configuration...`、`Loading MCP server: <name>`

## 6. 排查示例

```bash
# 1. 端口监听检查
ss -tlnp | grep -E '8091|8100|9222'

# 2. 健康检查
curl -s http://127.0.0.1:8091/health

# 3. 无头探测会 406（说明必须带 Accept 头）
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8091/mcp   # 406

# 4. 正确握手
curl -s -X POST http://127.0.0.1:8091/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-03-26","capabilities":{},
                 "clientInfo":{"name":"probe","version":"1.0"}}}'

# 5. 后端浏览器 MCP 是否存活
curl -s -X POST http://127.0.0.1:8100/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | jq -r '.result.tools[].name'

# 6. 看集线加载了哪些后端
tail -n 200 /var/log/gem/python-server.log | grep -i mcp
```

## 7. 相关脚本与目录

- `/opt/gem/gem.sh` — 环境初始化主脚本（负责渲染 mcp-hub.json、nginx 配置等）
- `/opt/gem/render-claude-config.sh` / `render-opencode-config.sh` — 客户端配置渲染
- `/opt/gem/nginx/` — nginx 各 location 配置（含 `mcp_hub.conf`）
- `/opt/gem/browser-ctl.sh` — 浏览器安装/配置
- `/opt/gem/agent-browser-init.sh` — 浏览器启动初始化
