"""Tests for HITL approval policy enforcement (``allowed_decisions``).

Covers:
- Config → SDK mapping: ``_map_interrupt_on`` produces ``InterruptOnConfig``
  dicts (allowed_decisions / description / args_schema) and rejects invalid
  decision lists instead of silently dropping them.
- Notifications: ``_build_approval_request_info`` carries each action's
  ``allowed_decisions`` so clients can render the right button set.
- Enforcement: ``submit_approval`` rejects decisions outside the current
  interrupt policy (403), rejects the unsupported ``respond`` decision
  (501), and accepts allowed decisions.
- Global security: ``load_approval_interrupt_rules`` propagates a decision
  whitelist and ``PUT /settings`` validates it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from agentcore.runtime import chat_router
from agentcore.runtime import security_router
from agentcore.runtime.agent_factory import _map_interrupt_on


# The shared conftest stubs ``load_security_settings`` to the default
# (approval off) for every test; the global-approval tests below need the
# real implementation.  Capture it at import time (before the autouse
# fixture runs) and restore it per-test.
_REAL_LOAD_SECURITY_SETTINGS = security_router.load_security_settings


# ---------------------------------------------------------------------------
# _map_interrupt_on — config → SDK interrupt_on mapping
# ---------------------------------------------------------------------------


class TestMapInterruptOn:
    def test_plain_rule_maps_to_true(self):
        """Legacy rules without allowed_decisions keep ``{tool: True}``."""
        result = _map_interrupt_on(
            [{"tool_name": "write_file", "require_approval": True}]
        )
        assert result == {"write_file": True}

    def test_rule_with_allowed_decisions_maps_to_config(self):
        """allowed_decisions produce an InterruptOnConfig-shaped dict."""
        result = _map_interrupt_on(
            [
                {
                    "tool_name": "write_file",
                    "require_approval": True,
                    "allowed_decisions": ["approve", "reject"],
                    "description": "Confirm file writes",
                }
            ]
        )
        assert result == {
            "write_file": {
                "allowed_decisions": ["approve", "reject"],
                "description": "Confirm file writes",
            }
        }

    def test_args_schema_passthrough(self):
        result = _map_interrupt_on(
            [
                {
                    "tool_name": "edit_file",
                    "require_approval": True,
                    "allowed_decisions": ["approve", "edit", "reject"],
                    "args_schema": {"type": "object"},
                }
            ]
        )
        assert result["edit_file"]["args_schema"] == {"type": "object"}

    def test_empty_allowed_decisions_raises(self):
        with pytest.raises(ValueError, match="allowed_decisions"):
            _map_interrupt_on(
                [
                    {
                        "tool_name": "write_file",
                        "require_approval": True,
                        "allowed_decisions": [],
                    }
                ]
            )

    def test_invalid_decision_raises(self):
        with pytest.raises(ValueError, match="Invalid"):
            _map_interrupt_on(
                [
                    {
                        "tool_name": "write_file",
                        "require_approval": True,
                        "allowed_decisions": ["approve", "delete"],
                    }
                ]
            )

    def test_rule_without_approval_skipped(self):
        assert (
            _map_interrupt_on(
                [{"tool_name": "write_file", "require_approval": False}]
            )
            is None
        )

    def test_empty_rules_none(self):
        assert _map_interrupt_on([]) is None

    def test_single_string_decision_normalised(self):
        result = _map_interrupt_on(
            [
                {
                    "tool_name": "write_file",
                    "require_approval": True,
                    "allowed_decisions": "approve",
                }
            ]
        )
        assert result["write_file"]["allowed_decisions"] == ["approve"]


# ---------------------------------------------------------------------------
# _build_approval_request_info — policy surfaced to clients
# ---------------------------------------------------------------------------


class TestApprovalRequestInfoPolicy:
    @staticmethod
    def _interrupt(actions: list[dict], review_configs: list[dict] | None = None):
        return SimpleNamespace(
            value={
                "action_requests": actions,
                "review_configs": review_configs or [],
            }
        )

    def test_allowed_decisions_carried_per_action(self):
        it = self._interrupt(
            [{"name": "write_file", "args": {"path": "/a"}, "description": "d"}],
            [
                {
                    "action_name": "write_file",
                    "allowed_decisions": ["approve", "reject"],
                }
            ],
        )
        info = chat_router._build_approval_request_info([it])
        assert info["actions"][0]["allowed_decisions"] == ["approve", "reject"]

    def test_missing_review_config_yields_none(self):
        it = self._interrupt(
            [{"name": "write_file", "args": {}, "description": ""}]
        )
        info = chat_router._build_approval_request_info([it])
        assert info["actions"][0]["allowed_decisions"] is None

    def test_multiple_actions_matched_by_name(self):
        it = self._interrupt(
            [
                {"name": "delete", "args": {}, "description": ""},
                {"name": "write_file", "args": {}, "description": ""},
            ],
            [
                {"action_name": "delete", "allowed_decisions": ["approve"]},
                {
                    "action_name": "write_file",
                    "allowed_decisions": ["approve", "reject"],
                },
            ],
        )
        info = chat_router._build_approval_request_info([it])
        by_name = {a["name"]: a["allowed_decisions"] for a in info["actions"]}
        assert by_name == {
            "delete": ["approve"],
            "write_file": ["approve", "reject"],
        }


# ---------------------------------------------------------------------------
# _get_interrupt_allowed_decisions — policy read from interrupt state
# ---------------------------------------------------------------------------


class TestGetInterruptAllowedDecisions:
    @staticmethod
    def _graph(interrupts: list[Any]):
        graph = MagicMock()
        graph.aget_state = AsyncMock(
            return_value=SimpleNamespace(interrupts=interrupts)
        )
        return graph

    def test_union_of_decisions(self):
        graph = self._graph(
            [
                SimpleNamespace(
                    value={
                        "review_configs": [
                            {
                                "action_name": "write_file",
                                "allowed_decisions": ["approve", "reject"],
                            }
                        ]
                    }
                ),
                SimpleNamespace(
                    value={
                        "review_configs": [
                            {
                                "action_name": "delete",
                                "allowed_decisions": ["approve"],
                            }
                        ]
                    }
                ),
            ]
        )
        assert asyncio.run(
            chat_router._get_interrupt_allowed_decisions(graph, "s1")
        ) == {
            "approve",
            "reject",
        }

    def test_no_policy_returns_none(self):
        graph = self._graph(
            [SimpleNamespace(value={"review_configs": []})]
        )
        assert asyncio.run(
            chat_router._get_interrupt_allowed_decisions(graph, "s1")
        ) is None

    def test_no_interrupts_returns_none(self):
        graph = self._graph([])
        assert asyncio.run(
            chat_router._get_interrupt_allowed_decisions(graph, "s1")
        ) is None

    def test_state_error_returns_none(self):
        graph = MagicMock()
        graph.aget_state = AsyncMock(side_effect=RuntimeError("boom"))
        assert asyncio.run(
            chat_router._get_interrupt_allowed_decisions(graph, "s1")
        ) is None


# ---------------------------------------------------------------------------
# submit_approval — API-level policy enforcement
# ---------------------------------------------------------------------------


class TestSubmitApprovalPolicy:
    """submit_approval must reject out-of-policy decisions."""

    @staticmethod
    def _setup(
        monkeypatch: Any,
        allowed: list[str] | None = None,
    ) -> tuple[Any, MagicMock]:
        store = SimpleNamespace(
            load_session=lambda sid: {"session_id": sid, "agent_id": "a"}
        )
        snapshot = SimpleNamespace(
            interrupts=[
                SimpleNamespace(
                    value={
                        "review_configs": [
                            {
                                "action_name": "write_file",
                                "allowed_decisions": allowed
                                or ["approve", "edit", "reject"],
                            }
                        ]
                    }
                )
            ]
        )
        graph = MagicMock()
        graph.aget_state = AsyncMock(return_value=snapshot)
        graph.ainvoke = AsyncMock(
            return_value={"messages": [], "__interrupt__": [], "usage_metadata": None}
        )
        chat_state = SimpleNamespace(
            graph_cache={"a": graph}, checkpointer=object()
        )
        req = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(chat_state=chat_state, store=store)
            )
        )
        # Stub downstream helpers so the happy path terminates cleanly.
        monkeypatch.setattr(
            chat_router, "_mark_last_pending_approval", lambda *a, **k: None
        )
        monkeypatch.setattr(
            chat_router, "_append_message", lambda *a, **k: None
        )
        monkeypatch.setattr(
            chat_router,
            "_extract_text_from_result",
            lambda r: ("ok", []),
        )
        monkeypatch.setattr(
            chat_router, "_extract_usage_from_result", lambda r: (0, 0)
        )
        monkeypatch.setattr(
            chat_router,
            "_get_first_interrupt_tool_name",
            lambda g, s: "write_file",
        )
        monkeypatch.setattr(
            chat_router, "_get_first_interrupt_args", lambda g, s: {}
        )
        # The audit log would write into the real data dir — bypass it.
        monkeypatch.setattr(
            "agentcore.runtime.audit.record_approval_decision",
            lambda **kw: None,
        )
        return req, graph

    @staticmethod
    def _submit(req: Any, decision: str = "approve", edited_args=None) -> Any:
        approval = chat_router.ApprovalRequest(
            decision=decision, edited_args=edited_args
        )
        return asyncio.run(chat_router.submit_approval("a", "s1", approval, req))

    def test_decision_outside_policy_rejected(self, monkeypatch):
        """edit must be refused when the policy only allows approve/reject."""
        req, graph = self._setup(monkeypatch, allowed=["approve", "reject"])
        with pytest.raises(HTTPException) as ei:
            self._submit(req, decision="edit", edited_args={"path": "/x"})
        assert ei.value.status_code == 403
        assert "not allowed" in ei.value.detail
        graph.ainvoke.assert_not_awaited()

    def test_respond_not_supported(self, monkeypatch):
        """respond is declared by the SDK but not implemented here."""
        req, graph = self._setup(monkeypatch)
        with pytest.raises(HTTPException) as ei:
            self._submit(req, decision="respond")
        assert ei.value.status_code == 501
        graph.ainvoke.assert_not_awaited()

    def test_allowed_decision_passes(self, monkeypatch):
        req, graph = self._setup(monkeypatch, allowed=["approve", "reject"])
        resp = self._submit(req, decision="approve")
        assert resp.status == "complete"
        assert resp.content == "ok"
        graph.ainvoke.assert_awaited_once()

    def test_unknown_decision_fails_validation(self):
        with pytest.raises(ValidationError):
            chat_router.ApprovalRequest(decision="delete")


# ---------------------------------------------------------------------------
# Global security rules — decision whitelist propagation
# ---------------------------------------------------------------------------


class TestSecurityGlobalAllowedDecisions:
    @pytest.fixture(autouse=True)
    def _restore_real_load_security_settings(self, monkeypatch):
        """Undo the conftest stub so file persistence is exercised."""
        monkeypatch.setattr(
            security_router,
            "load_security_settings",
            _REAL_LOAD_SECURITY_SETTINGS,
        )

    def test_load_rules_carries_whitelist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTCORE_DATA_DIR", str(tmp_path))
        security_router._save_security_settings(
            {
                "approval": {
                    "enabled": True,
                    "tools": ["delete"],
                    "allowed_decisions": ["approve", "reject"],
                }
            }
        )
        rules = security_router.load_approval_interrupt_rules()
        assert rules == [
            {
                "tool_name": "delete",
                "require_approval": True,
                "allowed_decisions": ["approve", "reject"],
            }
        ]

    def test_rules_without_whitelist_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTCORE_DATA_DIR", str(tmp_path))
        security_router._save_security_settings(
            {"approval": {"enabled": True, "tools": ["delete"]}}
        )
        rules = security_router.load_approval_interrupt_rules()
        assert rules == [{"tool_name": "delete", "require_approval": True}]

    def test_apply_global_approval_merges_whitelist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTCORE_DATA_DIR", str(tmp_path))
        security_router._save_security_settings(
            {
                "approval": {
                    "enabled": True,
                    "tools": ["delete"],
                    "allowed_decisions": ["approve"],
                }
            }
        )
        settings = {
            "interrupt_rules": [
                {"tool_name": "write_file", "require_approval": True}
            ]
        }
        merged = security_router.apply_global_approval(settings)
        delete_rule = next(
            r for r in merged["interrupt_rules"] if r["tool_name"] == "delete"
        )
        assert delete_rule["allowed_decisions"] == ["approve"]

    def test_put_settings_validates_whitelist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTCORE_DATA_DIR", str(tmp_path))
        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
        with pytest.raises(HTTPException) as ei:
            asyncio.run(
                security_router.update_security_settings(
                    {
                        "approval": {
                            "enabled": True,
                            "tools": ["delete"],
                            "allowed_decisions": ["approve", "nope"],
                        }
                    },
                    req,
                )
            )
        assert ei.value.status_code == 400

    def test_put_settings_accepts_whitelist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTCORE_DATA_DIR", str(tmp_path))
        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
        result = asyncio.run(
            security_router.update_security_settings(
                {
                    "approval": {
                        "enabled": True,
                        "tools": ["delete"],
                        "allowed_decisions": ["approve", "reject"],
                    }
                },
                req,
            )
        )
        assert result["approval"]["allowed_decisions"] == ["approve", "reject"]
        persisted = security_router.load_security_settings()
        assert persisted["approval"]["allowed_decisions"] == ["approve", "reject"]
