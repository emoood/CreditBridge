from ..config import Settings
from .base import LLMProvider
from .ollama_provider import OllamaProvider


def build_provider(settings: Settings, route: str | None = None) -> LLMProvider:
    route = route or settings.llm_default_route
    url, model = settings.route(route)
    return OllamaProvider(url, model, route, settings.llm_context_tokens,
                          settings.llm_temperature, settings.llm_call_timeout_seconds)
