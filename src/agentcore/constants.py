"""Project constants for AgentCore control plane."""

from __future__ import annotations

BUILT_IN_TOOLS: tuple[str, ...] = (
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "delete",
    "glob",
    "grep",
    "execute",
    "task",
)

WORKSPACE_MENU: tuple[str, ...] = (
    "files",
    "skills",
    "tools",
    "mcp",
    "acp",
    "run_config",
    "agent_stats",
)

SETTINGS_MENU: tuple[str, ...] = (
    "agent_management",
    "model_management",
    "skill_pool",
    "environment_variables",
    "security",
    "sandbox_control",
    "token_usage",
    "backup",
    "speech_transcription",
    "debugging",
)
