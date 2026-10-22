from ..config import Settings
from .base import LLMProvider
from .guard import GuardedProvider
from .ollama_provider import OllamaProvider
from .openai_compat import OpenAICompatProvider


def build_provider(settings: Settings, route: str | None = None) -> LLMProvider:
    route = route or settings.llm_default_route
    url, model = settings.route(route)
    limit = settings.llm_call_timeout_seconds
    if route == "lmstudio":
        inner: LLMProvider = OpenAICompatProvider(url, model, route, settings.llm_temperature, limit + 10)
    else:
        inner = OllamaProvider(url, model, route, settings.llm_context_tokens, settings.llm_temperature, limit + 10)
    return GuardedProvider(inner, limit)
