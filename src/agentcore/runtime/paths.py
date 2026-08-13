"""统一路径管理：所有 on-disk 布局的单一事实来源。

目录结构::

    .agentcore/                        ← get_data_dir()（可用 AGENTCORE_DATA_DIR 覆盖）
    ├── workspace/agent/{agent-id}/    ← agent 可见的 workspace（内核文件 / skills / agent.json）
    ├── data/agents/{agent-id}/        ← agent 运行时数据（sessions 等，agent 不可见）
    ├── skill_pool/                    ← 全局技能池（安装时复制到 agent workspace/skills/）
    └── checkpoints.db                 ← LangGraph checkpointer
"""

from __future__ import annotations

import os
from pathlib import Path


def get_data_dir() -> Path:
    """获取数据根目录（默认 ``.agentcore``，可用环境变量覆盖）。"""
    return Path(os.environ.get("AGENTCORE_DATA_DIR", ".agentcore"))


def get_workspace_root() -> Path:
    """获取 workspace 根目录：``.agentcore/workspace/agent``。"""
    return get_data_dir() / "workspace" / "agent"


def get_agent_workspace_dir(agent_id: str) -> Path:
    """获取单个 agent 的 workspace 目录。"""
    return get_workspace_root() / agent_id


def get_agent_data_dir(agent_id: str) -> Path:
    """获取 agent 的运行时数据目录（sessions 等）。"""
    return get_data_dir() / "data" / "agents" / agent_id


def get_agent_sessions_dir(agent_id: str) -> Path:
    """获取 agent 的 sessions 持久化目录。"""
    return get_agent_data_dir(agent_id) / "sessions"


def get_skill_pool_dir() -> Path:
    """获取全局技能池目录：``.agentcore/skill_pool``。"""
    return get_data_dir() / "skill_pool"


def get_checkpoints_path() -> Path:
    """获取 checkpointer 数据库路径。"""
    return get_data_dir() / "checkpoints.db"
