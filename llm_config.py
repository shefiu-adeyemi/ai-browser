"""
Builds the AI model client for the provider chosen in Settings (LLM_PROVIDER / LLM_MODEL).

Which providers exist, their default models and their key names all come from providers.py, so
this file, the settings screen and .env.example cannot drift apart again.

  claude    Anthropic Claude   ANTHROPIC_API_KEY   browser-use ChatAnthropic
  openai    OpenAI             OPENAI_API_KEY      browser-use ChatOpenAI  (native, no LiteLLM)
  gemini    Google Gemini      GEMINI_API_KEY      browser-use ChatGoogle
  deepseek  DeepSeek           DEEPSEEK_API_KEY    browser-use ChatDeepSeek (native, no LiteLLM)

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
