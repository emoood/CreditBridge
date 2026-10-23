from ..config import Settings
from .base import LLMProvider
from .gemini_provider import GeminiProvider
from .guard import GuardedProvider
from .ollama_provider import OllamaProvider
from .openai_compat import OpenAICompatProvider


def build_provider(settings: Settings, route: str | None = None) -> LLMProvider:
    route = route or settings.llm_default_route
    limit = settings.llm_call_timeout_seconds
    if route == "gemini":
        key = settings.get_gemini_api_key()
        inner: LLMProvider = GeminiProvider(
            api_key=key,
            model=settings.gemini_model,
            temperature=settings.llm_temperature,
            timeout=limit + 10,
            rpm=settings.gemini_rpm,
        )
    elif route == "lmstudio":
        url, model = settings.route(route)
        inner = OpenAICompatProvider(url, model, route, settings.llm_temperature, limit + 10)
    else:
        url, model = settings.route(route)
        inner = OllamaProvider(url, model, route, settings.llm_context_tokens, settings.llm_temperature, limit + 10,
                               keep_alive=settings.ollama_keep_alive, think=settings.llm_think)
    return GuardedProvider(inner, limit)
