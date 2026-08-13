"""Menu-driven information architecture for UI navigation."""

from __future__ import annotations

from dataclasses import dataclass

from agentcore.constants import SETTINGS_MENU, WORKSPACE_MENU


@dataclass(frozen=True)
class NavigationDomain:
    """One top-level navigation domain."""

    domain_key: str
    display_name: str
    children: tuple[str, ...]


def default_navigation_domains() -> tuple[NavigationDomain, ...]:
    """Return default control-plane navigation domains."""

    return (
        NavigationDomain("chat_inbox", "聊天与收件箱", ("chat", "inbox")),
        NavigationDomain("control", "控制", ("channels", "sessions", "scheduled_tasks", "heartbeat")),
        NavigationDomain("workspace", "工作区", WORKSPACE_MENU),
        NavigationDomain("settings", "设置", SETTINGS_MENU),
    )
