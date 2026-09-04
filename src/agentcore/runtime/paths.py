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


def get_project_root() -> Path:
    """获取项目根目录（``src/agentcore/runtime`` 向上三级）。"""
    return Path(__file__).resolve().parents[3]


def get_backend_log_file() -> Path:
    """获取后端文件日志路径：``<project-root>/logs/backend.log``。

    无论以何种方式启动（脚本重定向 / 直接运行），后端都会通过
    ``debug_router.setup_file_logging()`` 写入该文件，供调试页面读取。
    """
    return get_project_root() / "logs" / "backend.log"


# ---------------------------------------------------------------------------
# Checkpointer 后端配置（SQLite / PostgreSQL）
# ---------------------------------------------------------------------------


def get_checkpointer_backend() -> str:
    """获取 checkpointer 后端类型：``sqlite`` | ``postgresql``。

    通过环境变量 ``AGENTCORE_CHECKPOINTER_BACKEND`` 配置，默认 ``sqlite``。
    """
    return os.environ.get("AGENTCORE_CHECKPOINTER_BACKEND", "sqlite").lower()


def is_postgres_enabled() -> bool:
    """判断是否启用 PostgreSQL 作为 checkpointer 后端。"""
    return get_checkpointer_backend() == "postgresql"


def get_postgres_host() -> str:
    """获取 PostgreSQL 主机地址。"""
    return os.environ.get("AGENTCORE_POSTGRES_HOST", "localhost")


def get_postgres_port() -> str:
    """获取 PostgreSQL 端口。"""
    return os.environ.get("AGENTCORE_POSTGRES_PORT", "5432")


def get_postgres_db() -> str:
    """获取 PostgreSQL 数据库名。"""
    return os.environ.get("AGENTCORE_POSTGRES_DB", "agentcore")


def get_postgres_user() -> str:
    """获取 PostgreSQL 用户名。"""
    return os.environ.get("AGENTCORE_POSTGRES_USER", "agentcore")


def get_postgres_password() -> str:
    """获取 PostgreSQL 密码。"""
    return os.environ.get("AGENTCORE_POSTGRES_PASSWORD", "agentcore_secret")


def get_postgres_connection_string() -> str:
    """构建 PostgreSQL 连接字符串。

    格式：postgresql://user:password@host:port/dbname

    也可以通过 ``AGENTCORE_POSTGRES_URL`` 环境变量直接指定完整连接字符串。
    """
    # 优先使用完整连接字符串
    url = os.environ.get("AGENTCORE_POSTGRES_URL")
    if url:
        return url

    # 否则构建连接字符串
    user = get_postgres_user()
    password = get_postgres_password()
    host = get_postgres_host()
    port = get_postgres_port()
    db = get_postgres_db()
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"
