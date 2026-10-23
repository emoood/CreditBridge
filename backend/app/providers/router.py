from ..config import Settings
from .base import LLMProvider
from .gemini_provider import GeminiProvider
from .ollama_provider import OllamaProvider


def build_provider(settings: Settings, route: str | None = None) -> LLMProvider:
    route = route or settings.llm_default_route
    if route == "gemini":
        key = settings.get_gemini_api_key()
        return GeminiProvider(
            api_key=key,
            model=settings.gemini_model,
            temperature=settings.llm_temperature,
            timeout=settings.llm_call_timeout_seconds,
        )
    url, model = settings.route(route)
    return OllamaProvider(url, model, route, settings.llm_context_tokens,
                          settings.llm_temperature, settings.llm_call_timeout_seconds)
