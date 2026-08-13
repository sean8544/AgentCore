# AgentCore

本仓库是 AgentCore 管理平面的实现代码（deepagents 内核）。

## 本地启动（Windows）

在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1
```

可选参数：

```powershell
# 指定地址和端口
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1 -HostName 0.0.0.0 -Port 8000

# 安装开发依赖（含 pytest）
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1 -InstallDevDeps
```

启动后访问：

- 首页: http://127.0.0.1:8000/
- OpenAPI 文档: http://127.0.0.1:8000/docs

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