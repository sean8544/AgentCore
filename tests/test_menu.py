from __future__ import annotations

from agentcore.constants import SETTINGS_MENU, WORKSPACE_MENU
from agentcore.menu import default_navigation_domains


def test_menu_contains_workspace_and_settings_modules() -> None:
    domains = default_navigation_domains()
    workspace = next(item for item in domains if item.domain_key == "workspace")
    settings = next(item for item in domains if item.domain_key == "settings")

    assert workspace.children == WORKSPACE_MENU
    assert settings.children == SETTINGS_MENU
