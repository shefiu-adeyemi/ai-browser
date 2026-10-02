"""
Builds the AI model client for the provider chosen in Settings (LLM_PROVIDER / LLM_MODEL).

Which providers exist, their default models and their key names all come from providers.py, so
this file, the settings screen and .env.example cannot drift apart again.

  claude    Anthropic Claude   ANTHROPIC_API_KEY   browser-use ChatAnthropic
  openai    OpenAI             OPENAI_API_KEY      browser-use ChatOpenAI  (native, no LiteLLM)
  gemini    Google Gemini      GEMINI_API_KEY      browser-use ChatGoogle
  deepseek  DeepSeek           DEEPSEEK_API_KEY    browser-use ChatDeepSeek (native, no LiteLLM)
  qwen      Qwen (Alibaba)     QWEN_API_KEY        browser-use ChatOpenAI pointed at Alibaba's OpenAI-compatible endpoint

DeepSeek used to go through LiteLLM with a process-wide monkeypatch. The native client sends the
schema as a forced function call, which DeepSeek supports, so no workaround is needed. There is
deliberately no patched fallback: if a live DeepSeek model misbehaves, fix the cause.

`get_llm()` is the model for the Standard slot (LLM_PROVIDER / LLM_MODEL). `build_llm()` builds any
provider/model pair, which is what the model tiers (model_tiers.py) use for Fast and Strong.
"""

import os
from typing import Optional

import providers
from browser_use.llm.base import BaseChatModel

MAX_OUTPUT_TOKENS = 8192

# Alibaba Cloud Model Studio's OpenAI-compatible endpoint. The key belongs to ONE region: an international key
# (the default) does not work against the mainland endpoint or the other way round. Set QWEN_BASE_URL for the
# mainland one: https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def vision_override() -> Optional[bool]:
    """The user's own answer from Settings ("this model can / cannot see screenshots"), or None for automatic."""
    value = (os.getenv("LLM_VISION") or "").strip().lower()
    return True if value == "1" else False if value == "0" else None


def _is_standard_model(provider_id: str, model: Optional[str]) -> bool:
    """Is this the Standard model chosen in Settings (the one the vision answer in Settings is about)?"""
    configured = providers.resolve_provider_id(os.getenv("LLM_PROVIDER", ""))
    if configured != provider_id:
        return False
    default = providers.BY_ID[provider_id].default_model
    wanted = providers.strip_model_prefix(os.getenv("LLM_MODEL", "")) or default
    return (providers.strip_model_prefix(model or "") or default).lower() == wanted.lower()


def supports_vision(provider_id: str, model: Optional[str] = None) -> bool:
    """Can this model take screenshots? What Settings says wins for the Standard model; otherwise the app's
    own knowledge (builtin_vision) decides. When a model can't see, the agent works from the page's text and
    element list alone (browser-use's main way of reading a page) and the end-of-run check is off."""
    override = vision_override()
    if override is not None and _is_standard_model(provider_id, model):
        return override
    return builtin_vision(provider_id, model)


def builtin_vision(provider_id: str, model: Optional[str] = None) -> bool:
    """The app's own knowledge of which models can see (what Settings shows as "automatic"). Unknown models
    are assumed NOT to, because a text-only model sent an image fails the whole run."""
    if provider_id == "deepseek":
        return False
    if provider_id == "qwen":
        name = providers.strip_model_prefix(model or "").lower() or providers.BY_ID["qwen"].default_model
        # The Qwen3.7 "plus" tier and every "-vl-" / omni model are multimodal; "max" and plain text models are not.
        return "-vl-" in name or "omni" in name or name.startswith("qwen3.7-plus")
    return True


class LLMConfigError(ValueError):
    """A problem with the AI settings that the user can fix; the message says how."""


def has_key(provider_id: str) -> bool:
    """Is an API key set for this provider?"""
    provider = providers.BY_ID.get(provider_id)
    return bool(provider and (os.getenv(provider.key_env) or "").strip())


def build_llm(provider_id: str, model: Optional[str] = None) -> BaseChatModel:
    """The client for one provider and model (the provider's default model if none is given)."""
    provider = providers.BY_ID.get(provider_id)
    if provider is None:
        raise LLMConfigError(
            f"Unknown AI provider '{provider_id}'. Choose one of: "
            + ", ".join(p.id for p in providers.PROVIDERS) + " (in Settings).")
    model = providers.strip_model_prefix(model or "") or provider.default_model
    api_key = (os.getenv(provider.key_env) or "").strip()
    if not api_key:
        raise LLMConfigError(f"No {provider.label} API key is set. Add it in Settings "
                             f"(or set {provider.key_env}).")

    if provider_id == "claude":
        from browser_use.llm import ChatAnthropic
        return ChatAnthropic(model=model, api_key=api_key, max_tokens=MAX_OUTPUT_TOKENS)

    if provider_id == "openai":
        from browser_use.llm import ChatOpenAI
        return ChatOpenAI(model=model, api_key=api_key, max_completion_tokens=MAX_OUTPUT_TOKENS)

    if provider_id == "gemini":
        from browser_use.llm import ChatGoogle
        return ChatGoogle(model=model, api_key=api_key, max_output_tokens=MAX_OUTPUT_TOKENS)

    if provider_id == "qwen":
        from browser_use.llm import ChatOpenAI
        # frequency_penalty is browser-use's guard for an OpenAI quirk; Alibaba's endpoint doesn't need it.
        return ChatOpenAI(model=model, api_key=api_key, base_url=(os.getenv("QWEN_BASE_URL") or "").strip() or QWEN_BASE_URL,
                          max_completion_tokens=MAX_OUTPUT_TOKENS, frequency_penalty=None)

    # deepseek
    from browser_use.llm.deepseek.chat import ChatDeepSeek
    return ChatDeepSeek(model=model, api_key=api_key, max_tokens=MAX_OUTPUT_TOKENS)


def get_llm() -> BaseChatModel:
    """The Standard model: the provider and model chosen in Settings."""
    raw_provider = os.getenv("LLM_PROVIDER", "")
    provider_id = providers.resolve_provider_id(raw_provider)
    if provider_id is None:
        raise LLMConfigError(
            f"Unknown AI provider '{raw_provider.strip()}'. Choose one of: "
            + ", ".join(p.id for p in providers.PROVIDERS) + " (in Settings).")
    return build_llm(provider_id, os.getenv("LLM_MODEL", ""))
