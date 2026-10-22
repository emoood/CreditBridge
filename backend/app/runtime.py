"""Process-wide runtime objects (catalog + retriever + precedent memory) and the provider factory (overridable in tests)."""
from collections.abc import Callable

from .agents.precedent import PrecedentStore
from .config import get_settings
from .providers.base import LLMProvider
from .providers.router import build_provider
from .services.catalog_retriever import CatalogRetriever
from .services.demo_repo import DemoRepo
from .services.policy_rules import Policy


class Runtime:
    def __init__(self) -> None:
        self.repo = DemoRepo(catalog=get_settings().catalog)
        self.retriever = CatalogRetriever(list(self.repo.targets.values()), Policy.load().non_transferable_departments)
        self.precedent = PrecedentStore()


_rt: Runtime | None = None
provider_override: Callable[[], LLMProvider] | None = None  # tests inject a guarded fake here


def rt() -> Runtime:
    global _rt
    if _rt is None:
        _rt = Runtime()
    return _rt


def reset_runtime() -> None:
    global _rt
    _rt = None


def get_provider() -> LLMProvider:
    return provider_override() if provider_override else build_provider(get_settings())
