from app.core.config import Settings
from app.llm.anthropic import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.openai_compatible import OpenAICompatibleProvider


def create_provider(
    *,
    provider: str,
    api_base: str,
    api_key: str,
    model: str,
    settings: Settings,
) -> LLMProvider:
    if provider == "anthropic":
        return AnthropicProvider(
            api_base,
            api_key,
            model,
            settings.llm_timeout_seconds,
            settings.llm_max_retries,
            settings.llm_max_tokens,
        )
    return OpenAICompatibleProvider(
        api_base,
        api_key,
        model,
        settings.llm_timeout_seconds,
        settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
        thinking_enabled=settings.llm_thinking_enabled,
    )
