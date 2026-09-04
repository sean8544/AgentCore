# AgentCore

本仓库是 AgentCore 管理平面的实现代码（deepagents 内核）。

## 安装

### 方式一：Shell 一键安装（Linux / macOS）

```bash
curl -fsSL <install-url>/install.sh | bash
# 或从本地源码安装:
bash scripts/install.sh --from-source /path/to/AgentCore
```

安装器自动完成：uv 安装 → Python 3.12 环境 → 克隆 AgentCore 源码 → 依赖安装 → 前端构建 → PATH 配置。
用户无需预装 Python，uv 全权管理。

### 方式二：PowerShell 一键安装（Windows 推荐）

```powershell
irm <install-url>/install.ps1 | iex
# 或本地执行:
.\scripts\install.ps1
# 或从本地源码安装:
.\scripts\install.ps1 -SourceDir D:\path\to\AgentCore
```

支持参数：`-SourceDir`、`-Extras`、`-UvPath`。
自动处理执行策略、uv 安装（含 GitHub Releases 国内回退）。

### 方式三：CMD 安装（Windows CMD）

```cmd
scripts\install.bat
:: 或从本地源码安装:
scripts\install.bat -SourceDir D:\path\to\AgentCore
```

### 方式四：Docker 部署

```bash
# 快速启动
cp .env.example .env   # 编辑填入 API Keys
docker compose up -d

# 或使用构建脚本（支持子路径部署）
bash scripts/docker_build.sh agentcore:v0.1.0
```

安装完成后运行 `agentcore-api` 启动服务，默认监听 `0.0.0.0:8000`（对本机所有网卡开放，局域网可访问），浏览器访问 http://127.0.0.1:8000/。仅监听本机请用 `scripts/start-local.*`（绑定 `127.0.0.1`）。

---

## Docker 部署详解

### 构建镜像

```bash
docker build -t agentcore .
# 或使用构建脚本:
bash scripts/docker_build.sh agentcore:latest
```

### 启动服务

```bash
# 1. 复制环境变量文件
cp .env.example .env
# 编辑 .env 填入 API Keys

# 2. 启动
docker compose up -d

# 3. 查看日志
docker compose logs -f
```

### 访问

- 前端: http://localhost:8000/
- API 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

### 停止服务

```bash
docker compose down
```

### 数据持久化

所有数据（agent 配置、会话、技能池等）存储在 Docker volume `agentcore-data` 中，容器删除后数据不会丢失。

```bash
# 查看 volume
docker volume ls | grep agentcore

# 备份数据
docker run --rm -v agentcore-data:/data -v $(pwd):/backup alpine tar czf /backup/agentcore-backup.tar.gz /data
```

### WSL2 中使用

在 WSL2 中可以直接使用 Docker Desktop 或原生 Docker：

```bash
# 确保 Docker 正在运行
docker ps

# 构建并启动
docker compose up -d --build
```

## K8s 子路径多实例部署（AGENTCORE_BASE_PATH）

同一个镜像可以在任意 URL 子路径下运行，支持在 K8s 中跑 N 个实例、按路径区分：

```
demo.k8s.com/instance01/...   → instance01 容器
demo.k8s.com/instance02/...   → instance02 容器
```

前缀由程序本体感知（后端 middleware 剥离前缀 + 前端运行时注入），**Ingress 只需原样转发，无需 rewrite**。

### 配置方式

给容器设置环境变量 `AGENTCORE_BASE_PATH`（带不带首尾斜杠均可，会自动规范化）：

```yaml
env:
  - name: AGENTCORE_BASE_PATH
    value: /instance01
```

也可在构建时固化到镜像：

```bash
AGENTCORE_BASE_PATH=/instance01 bash scripts/docker_build.sh agentcore-instance01:latest
# 或 docker build --build-arg AGENTCORE_BASE_PATH=/instance01 -t agentcore-instance01 .
```

- **不设或为空** → 根路径部署，行为与旧版本完全一致（现有 docker compose 部署零影响）。
- 设置后 → 应用整体挂载在该前缀下：`/instance01/`（前端）、`/instance01/api/*`（API）、`/instance01/health` 等。
- **不带前缀的请求依然放行**（如直接打 Pod 的 `/health`），K8s liveness/readiness 探针无需任何特殊配置。

### Ingress 示例（原样转发）

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: agentcore
spec:
  rules:
  - host: demo.k8s.com
    http:
      paths:
      - path: /instance01
        pathType: Prefix
        backend:
          service: { name: agentcore-instance01, port: { number: 8000 } }
      - path: /instance02
        pathType: Prefix
        backend:
          service: { name: agentcore-instance02, port: { number: 8000 } }
```

### 数据隔离

每个实例必须挂载**独立的持久卷**到 `AGENTCORE_DATA_DIR`（默认 `/app/.agentcore`）。SQLite checkpoint 不支持多实例共享写入，不要多 Pod 挂同一个卷。

### 本地模拟子路径

```powershell
# 方式 1：后端直接带前缀启动（需已构建前端）
$env:AGENTCORE_BASE_PATH='/dev'; uvicorn agentcore.api:app --port 8000
# 访问 http://127.0.0.1:8000/dev/

# 方式 2：Vite dev server
$env:VITE_BASE_PATH='/dev'; npm run dev   # 在 console/ 目录，访问 http://localhost:3000/dev/
```

### 实现要点（供维护者参考）

- 后端：`api.py` 的 `BasePathMiddleware` 剥离请求前缀；`_SpaStaticFiles` 吐 index.html 时把构建产物中的 `__AGENTCORE_BASE_PATH__` 占位符替换为实际值（含带斜杠形式，避免双斜杠）
- 前端：Vite 构建 `base` 为占位符，运行时经 `window.__AGENTCORE_BASE_PATH__` 注入；`apiClient.baseURL`、`BrowserRouter basename`、localStorage key 均基于该值（同域多实例存储自动隔离）
- 冒烟测试：`.pytest_tmp/smoke_base_path.py`（带前缀）、`.pytest_tmp/smoke_root_path.py`（根路径兼容性）

## 本地开发启动

### Windows

```powershell
# 方式 1：一键启动（自动检查环境、安装依赖、启动服务）
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1

# 方式 2：使用安装器安装后启动
agentcore-api
```

### Linux / macOS

```bash
# 方式 1：一键启动
bash scripts/start-local.sh

# 方式 2：使用安装器安装后启动
agentcore-api
```

启动后访问：

- 首页: http://127.0.0.1:8000/
- OpenAPI 文档: http://127.0.0.1:8000/docs

> `start-local.*` 以后台进程方式启动服务（日志写入 `logs/`），脚本退出后进程仍在运行，需用下方「停止服务」脚本关闭。

## 本地停止服务

### Windows

```powershell
.\scripts\stop-local.ps1                    # 停止后端 + 前端，并清理孤儿 worker
.\scripts\stop-local.ps1 -BackendOnly       # 只停后端
.\scripts\stop-local.ps1 -FrontendOnly      # 只停前端
.\scripts\stop-local.ps1 -BackendPort 8001  # 指定后端端口
```

### Linux / macOS

```bash
bash scripts/stop-local.sh                  # 停止后端 + 前端
bash scripts/stop-local.sh --backend-only   # 只停后端
bash scripts/stop-local.sh --frontend-only  # 只停前端
```

### ⚠️ Windows `--reload` 孤儿 worker 陷阱

开发时若手动用 `uvicorn ... --reload` 启动，父进程（reloader）被杀后其 `multiprocessing.spawn` 子 worker 会变成**孤儿进程继续存活并占用端口**，以旧代码响应请求 —— 表现为「改了代码 / 删了 agent，重启后仍不生效」。`Get-NetTCPConnection` 只显示其中一个 PID，肉眼难以发现。

`stop-local.ps1` 默认会扫描并清理这类孤儿 worker（命令行含 `multiprocessing.spawn` 且父进程已退出）。手动排查：

```powershell
# 列出所有 uvicorn / spawn 进程，父 PID 不在进程表中的即孤儿
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'uvicorn agentcore|multiprocessing.spawn' } |
  Select-Object ProcessId, ParentProcessId, CreationDate, CommandLine
# 清理：taskkill /PID <pid> /T /F   （/T 连带终止子进程树）
```

推荐用 `start-local.*`（`--workers 1`、无 `--reload`）启动，可从根源避免该问题；需热重载时务必配合 `stop-local.ps1` 停止。

## 本地启动 OpenSandbox（可选，沙箱功能）

[OpenSandbox](https://github.com/alibaba/OpenSandbox) 是一个**独立的第三方服务**，AgentCore 通过其 SDK 调用它来运行沙箱容器。本仓库仅提供一个**引导型**快速启动脚本方便新人本地跑通，不复定义 server 运行参数、不接管其进程生命周期（配置由官方 `init-config` 生成，不内嵌镜像 tag；脚本前台运行，`Ctrl+C` 即停）。

前置条件：已安装并可运行 Docker（Windows 需启用 WSL2）、以及 `uv`/`uvx`（`pip install uv`）。

```powershell
# Windows PowerShell
.\scripts\opensandbox-quickstart.ps1
```

```bash
# Linux / macOS
bash scripts/opensandbox-quickstart.sh
```

脚本会：校验 Docker / uv → 仅当 `~/.sandbox.toml` 不存在时执行 `uvx opensandbox-server init-config ~/.sandbox.toml --example docker` → **自动将 `[docker].seccomp_profile` 写入 `"unconfined"`** → 前台启动 `uvx opensandbox-server`（默认监听 `http://localhost:8080`）。

> **AIO Sandbox 必备：`seccomp_profile = "unconfined"`** —— `ghcr.io/agent-infra/sandbox` 镜像内部自带 Chromium / Playwright / VNC，启动时需要 Docker 官方默认 seccomp 之外的 `clone3`、`unshare` 等系统调用，否则浏览器相关工具会因 seccomp 封锁而启动失败。等价于 `docker run --security-opt seccomp=unconfined`。该开关是 **OpenSandbox server 全局配置**（SDK 建箱参数里没有也不应该有），写入 `~/.sandbox.toml` 的 `[docker]` 节后需**重启 server** 才对新建容器生效；已存量的 sandbox 需销毁后重建。若你手工跑过 `init-config`，请自行到 `~/.sandbox.toml` 把 `seccomp_profile` 从 `""` 改为 `"unconfined"`（引导脚本已自动完成）。

启动后，在 AgentCore 的 **设置 → 沙箱控制平面** 页面填写服务器 URL 并点击"启用连接"；若服务器不可达，该页会展示上述可复制的官方启动命令与文档链接。

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Agent 生命周期

AgentCore 的 agent 基于 deepagents SDK，采用**请求驱动**模型：每个 agent 没有常驻后台循环，每次调用在当次请求内执行图计算并返回，请求之间不运行任何 agent 逻辑。

状态与操作语义：

- **idle** — 无在途请求，是 agent 的正常静息状态（不代表未启动或异常）。
- **start**（`POST /api/agents/{id}/start`）— 预热：预先构建 agent 图 / 加载 workspace 到内存，加快首次响应。
- **stop**（`POST /api/agents/{id}/stop`）— 卸载：释放内存态（workspace 从内存移除并刷盘、清除图缓存），**不删除磁盘文件**；下次使用时会懒加载重新构建。
- **delete**（`DELETE /api/agents/{id}`）— 唯一会删除 workspace 磁盘目录（rmtree）的操作。

首次对话引导：新建 agent 的 workspace 会播种 `bootstrap.md` 引导文件（通过 `memory=[]` 注入 system_prompt），引导 agent 完成身份/偏好设置；agent 按指引用 delete 工具删除该文件后即标志初始化完成，后端检测到删除会自动失效图缓存，下一轮对话以新的人设重建 agent，且 `bootstrap.md` 永远不会被重新创建。