"""Model factory: resolves profile model settings to a LangChain chat model.

deepagents' ``create_deep_agent`` accepts either a ``provider:model`` string
(resolved via ``langchain.init_chat_model``) or a pre-built
``BaseChatModel`` instance.  String resolution cannot express custom
``base_url`` / ``api_key`` combinations, which are required for
OpenAI-compatible gateways such as Aliyun DashScope (Qwen/通义千问).

This module builds a :class:`langchain_openai.ChatOpenAI` instance for any
OpenAI-compatible configuration found in a profile's ``settings["model"]``:

.. code-block:: python

    {
        "provider": "openai",          # or "qwen" / "dashscope"
        "name": "qwen3.6-plus",
        "base_url": "https://coding.dashscope.aliyuncs.com/v1",
        "api_key": "sk-...",           # optional; env vars checked too
    }

API key lookup order:

1. ``model_cfg["api_key"]`` (stored in the profile settings)
2. ``model_cfg["api_key_env"]`` — a custom env var name from agent.json
3. ``AGENTCORE_LLM_API_KEY``
4. Provider-specific env vars: ``DASHSCOPE_API_KEY`` / ``QWEN_API_KEY``
5. ``OPENAI_API_KEY``
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

# Provider names treated as Qwen/DashScope-flavoured OpenAI-compatible APIs.
_QWEN_PROVIDER_ALIASES = frozenset(
    {"qwen", "tongyi", "dashscope", "aliyun", "aliyun_codingplan", "aliyun-codingplan"}
)

# Default endpoints per provider family.
_QWEN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# Env var names checked for an API key, in priority order.
_API_KEY_ENV_VARS = (
    "AGENTCORE_LLM_API_KEY",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "OPENAI_API_KEY",
)


def _resolve_api_key(model_cfg: dict[str, Any]) -> str | None:
    """Return the API key from the config dict or known env vars."""
    explicit = model_cfg.get("api_key")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    # A config-declared env var name (agent.json's ``api_key_env``) takes
    # precedence over the built-in fallback list.
    env_var = model_cfg.get("api_key_env")
    candidates: tuple[Any, ...] = (
        (env_var,) + _API_KEY_ENV_VARS
        if isinstance(env_var, str) and env_var.strip()
        else _API_KEY_ENV_VARS
    )
    for var in candidates:
        value = os.environ.get(str(var))
        if value and value.strip():
            return value.strip()
    return None


# ---------------------------------------------------------------------------
# Client cache
#
# Constructing a ChatOpenAI client per agent-graph build is wasteful and
# defeats HTTP connection pooling.  Built clients are cached by a key that
# includes a hash of the resolved API key, so rotating the key (config or
# env) transparently invalidates the cached client.
# ---------------------------------------------------------------------------

_model_cache: dict[str, BaseChatModel] = {}


def clear_model_cache() -> None:
    """Drop all cached chat-model clients (used by tests and key rotation)."""
    _model_cache.clear()


def _cache_key(
    provider: str,
    name: str,
    base_url: str | None,
    api_key: str | None,
    temperature: float | None,
) -> str:
    key_digest = (
        hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
        if api_key
        else "-"
    )
    return (
        f"{provider}:{name}:{base_url or ''}:{key_digest}"
        f":{temperature if temperature is not None else ''}"
    )


def build_chat_model(model_cfg: dict[str, Any] | str | None) -> BaseChatModel | None:
    """Build a chat model from profile model settings.

    Returns ``None`` when the configuration is missing/incomplete and the
    caller should fall back to a plain ``provider:model`` string (or the
    SDK default).
    """
    if model_cfg is None:
        return None

    # Normalise string form ("provider:model") into a dict.
    if isinstance(model_cfg, str):
        spec = model_cfg.strip()
        if not spec:
            return None
        provider, _, name = spec.partition(":")
        if not _:
            return None  # bare model name — let init_chat_model handle it
        model_cfg = {"provider": provider, "name": name}

    provider = str(model_cfg.get("provider") or "").strip().lower()
    name = str(model_cfg.get("name") or "").strip()
    base_url = str(model_cfg.get("base_url") or "").strip() or None
    if not name:
        return None

    is_qwen = provider in _QWEN_PROVIDER_ALIASES
    if not (is_qwen or base_url or provider == "openai"):
        # Not an OpenAI-compatible configuration we can construct; fall back
        # to the string path (init_chat_model) in the caller.
        return None

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        logger.warning(
            "langchain-openai is not installed; cannot build OpenAI-compatible "
            "model for provider %r. Run: pip install langchain-openai",
            provider,
        )
        return None

    api_key = _resolve_api_key(model_cfg)
    resolved_base_url = base_url or (_QWEN_DEFAULT_BASE_URL if is_qwen else None)
    temperature = model_cfg.get("temperature")
    if not isinstance(temperature, (int, float)):
        temperature = None

    # Reuse a previously built client when the identity (provider / model /
    # endpoint / key / temperature) is unchanged.
    cache_key = _cache_key(
        provider, name, resolved_base_url, api_key, temperature
    )
    cached = _model_cache.get(cache_key)
    if cached is not None:
        logger.debug("Reusing cached ChatOpenAI client (%s)", cache_key)
        return cached

    kwargs: dict[str, Any] = {"model": name}
    if resolved_base_url:
        kwargs["base_url"] = resolved_base_url
    if api_key:
        kwargs["api_key"] = api_key
    if temperature is not None:
        kwargs["temperature"] = float(temperature)

    logger.info(
        "Building ChatOpenAI(model=%s, base_url=%s, api_key=%s)",
        name,
        resolved_base_url or "<default>",
        "<set>" if api_key else "<unset>",
    )
    model = ChatOpenAI(**kwargs)
    _model_cache[cache_key] = model
    return model


def build_model_string(model_cfg: dict[str, Any] | str | None) -> str | None:
    """Derive a ``provider:model`` string for *create_deep_agent*.

    Accepts either a dict ``{"provider": "...", "name": "..."}`` or a
    pre-formatted string.  Returns ``None`` when no model information is
    available.
    """
    if model_cfg is None:
        return None
    if isinstance(model_cfg, str):
        return model_cfg or None
    provider = model_cfg.get("provider")
    name = model_cfg.get("name")
    if not provider or not name:
        return None
    return f"{provider}:{name}"


def resolve_model(model_cfg: dict[str, Any] | str | None) -> BaseChatModel | str | None:
    """Resolve profile model settings to the best available representation.

    Preference order:

    1. A fully-constructed ``BaseChatModel`` (supports custom base_url/key).
    2. A ``provider:model`` string for ``init_chat_model`` resolution.
    3. ``None`` — caller decides how to handle the missing model.
    """
    model = build_chat_model(model_cfg)
    if model is not None:
        return model
    return build_model_string(model_cfg)
