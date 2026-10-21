"""
The single source of truth for which AI providers exist and what their defaults are.

Before this, the same facts were written out separately in llm/config.py (defaults), .env.example
(a different DeepSeek default), and the React UI (a different provider name, "anthropic" where the
server only knows "claude", plus its own model lists), so they had drifted apart. Everything now
reads this table; the server sends it to the UI, and a test checks .env.example against it.

Deliberately has no heavy imports (no browser-use, no AI SDKs) so the settings code can use it.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    key_env: str
    default_model: str
    suggestions: tuple[str, ...]      # shown in the UI; any other model name can still be typed in .env


PROVIDERS: tuple[Provider, ...] = (
    Provider("claude", "Claude (Anthropic)", "ANTHROPIC_API_KEY", "claude-sonnet-5-5",
             ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-haiku-4-5-20251001")),
    Provider("openai", "OpenAI", "OPENAI_API_KEY", "gpt-4o",
             ("gpt-4o", "gpt-4.1", "gpt-5", "gpt-5-mini", "o3")),
    Provider("gemini", "Gemini (Google)", "GEMINI_API_KEY", "gemini-2.5-flash",
             ("gemini-2.5-flash", "gemini-2.5-pro")),
    Provider("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "deepseek-v4-pro",
             ("deepseek-v4-pro", "deepseek-v4-flash")),
    # Qwen (Alibaba Cloud Model Studio / DashScope) through its OpenAI-compatible endpoint. The default is
    # the mid-priced multimodal model: it sees screenshots and is built for browser and GUI agent work, at
    # roughly a tenth of the price of the big Western models. qwen3.7-max is stronger but dearer, the qwen3-vl
    # models are older and cheaper; llm_config.supports_vision() says which of them can take screenshots.
    Provider("qwen", "Qwen (Alibaba)", "QWEN_API_KEY", "qwen3.7-plus",
             ("qwen3.7-plus", "qwen3-vl-plus", "qwen3-vl-flash", "qwen3.7-max")),
)

BY_ID = {p.id: p for p in PROVIDERS}
DEFAULT_PROVIDER = "claude"

# Names people naturally type, or that older versions of the UI saved.
_ALIASES = {"anthropic": "claude", "google": "gemini", "alibaba": "qwen", "dashscope": "qwen"}

# Older setups used LiteLLM-style names such as "deepseek/deepseek-v4-pro".
_MODEL_PREFIXES = ("anthropic/", "claude/", "openai/", "gemini/", "google/", "deepseek/", "qwen/", "alibaba/")


def resolve_provider_id(raw: Optional[str]) -> Optional[str]:
    """'Claude', 'anthropic', ' deepseek ' -> the canonical id; anything unknown -> None."""
    name = (raw or "").strip().lower()
    if not name:
        return DEFAULT_PROVIDER
    name = _ALIASES.get(name, name)
    return name if name in BY_ID else None


def strip_model_prefix(model: Optional[str]) -> str:
    """'deepseek/deepseek-v4-pro' -> 'deepseek-v4-pro' (the native clients want the bare name)."""
    name = (model or "").strip()
    lowered = name.lower()
    for prefix in _MODEL_PREFIXES:
        if lowered.startswith(prefix):
            return name[len(prefix):]
    return name


def ui_description() -> list[dict]:
    """What the settings screen needs to build its provider and model pickers."""
    return [{"id": p.id, "label": p.label, "key_env": p.key_env,
             "default_model": p.default_model, "models": list(p.suggestions)} for p in PROVIDERS]
