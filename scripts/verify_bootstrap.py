"""Task #34 验证：Default Agent + bootstrap.md 引导流程（全新安装模拟）。

使用临时数据目录模拟全新安装，验证：
1. 启动后自动创建 "default" agent
2. workspace 目录包含 agent.md / profile.md / soul.md / bootstrap.md / skills/
3. GET /api/agents 返回 default agent
4. bootstrap.md 内容会进入 memory=[] 注入（系统提示词）
5. 幂等性：二次启动不重复创建、不覆盖已有文件
6. 删除 bootstrap.md 后不会重新生成
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="agentcore_bootstrap_"))
os.environ["AGENTCORE_DATA_DIR"] = str(tmp / ".agentcore")

from fastapi.testclient import TestClient  # noqa: E402

from agentcore.api import app  # noqa: E402
from agentcore.runtime import paths  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


ws_dir = paths.get_agent_workspace_dir("default")

# --- 1) 全新启动 -----------------------------------------------------------
with TestClient(app) as client:
    # 1. default agent 自动创建
    agents = client.get("/api/agents").json()
    ids = [a["agent_id"] for a in agents]
    check("GET /api/agents 包含 default", "default" in ids, str(agents))

    # 2. workspace 文件结构
    for name in ("agent.md", "profile.md", "soul.md", "bootstrap.md", "agent.json"):
        check(f"workspace 包含 {name}", (ws_dir / name).exists())
    check("workspace 包含 skills/", (ws_dir / "skills").is_dir())
    check("workspace 包含 .bootstrap_seeded 标记", (ws_dir / ".bootstrap_seeded").exists())

    bootstrap_before = (ws_dir / "bootstrap.md").read_text(encoding="utf-8")
    check("bootstrap.md 含引导语", "欢迎使用 AgentCore" in bootstrap_before)

    # 3. kernel 文件 API 可读 bootstrap.md
    r = client.get("/api/agents/default/kernel/bootstrap.md")
    check("GET kernel/bootstrap.md 成功", r.status_code == 200 and "欢迎" in r.json()["content"])

    # 4. 注入验证：system_prompt / memory 包含 bootstrap 内容
    manager = client.app.state.agent_manager
    workspace = manager.get_workspace("default")
    prompt = workspace.get_system_prompt()
    check("get_system_prompt 注入 bootstrap.md", "欢迎使用 AgentCore" in prompt)

    # agent_factory 的 memory=[] 注入列表包含 /bootstrap.md
    from agentcore.runtime.workspace import KERNEL_FILE_NAMES
    memory_paths = [
        f"/{name}" for name in KERNEL_FILE_NAMES
        if (ws_dir / name).exists()
    ]
    check("memory=[] 包含 /bootstrap.md", "/bootstrap.md" in memory_paths, str(memory_paths))

    # 5. 用户编辑过的 agent.md 不被覆盖（模拟修改后重启）
    (ws_dir / "agent.md").write_text("# 自定义人设\n", encoding="utf-8")

# --- 6) 二次启动：幂等 + 不覆盖 ---------------------------------------------
with TestClient(app) as client:
    agents = client.get("/api/agents").json()
    check("二次启动仅一个 default agent", [a["agent_id"] for a in agents] == ["default"], str(agents))
    check("二次启动不覆盖已修改的 agent.md", (ws_dir / "agent.md").read_text(encoding="utf-8") == "# 自定义人设\n")
    check("二次启动不覆盖 bootstrap.md", (ws_dir / "bootstrap.md").read_text(encoding="utf-8") == bootstrap_before)

    # 7) 删除 bootstrap.md（模拟 agent 完成设置后删除）
    (ws_dir / "bootstrap.md").unlink()

# --- 三次启动：确认 bootstrap.md 不会被重新生成 ------------------------------
with TestClient(app) as client:
    check("删除 bootstrap.md 后不重新生成", not (ws_dir / "bootstrap.md").exists())
    agents = client.get("/api/agents").json()
    check("删除 bootstrap 后 agent 仍存在", any(a["agent_id"] == "default" for a in agents))

shutil.rmtree(tmp, ignore_errors=True)
print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部验证通过")
