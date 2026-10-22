from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMRequest:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    stage: str = ""  # e.g. "matching", "review"; used in the dev console
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    provider: str
    model: str
    duration_s: float
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    thinking: str = ""


@dataclass
class ProviderHealth:
    ok: bool
    provider: str
    model: str
    detail: str = ""
    loaded: bool | None = None
    size_mb: float | None = None
    processor: str | None = None


class LLMProvider(Protocol):
    name: str
    model: str

    async def health(self) -> ProviderHealth: ...
    async def complete(self, request: LLMRequest) -> LLMResponse: ...
