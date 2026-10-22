"""Scripted provider for tests ONLY. Never used to present output as a genuine generation."""
from .base import LLMRequest, LLMResponse, ProviderHealth, ToolCall


class FakeLLMProvider:
    name = "fake:fixture_test"
    model = "fake"

    def __init__(self, script: list[LLMResponse | list[ToolCall] | str]):
        self.script, self.requests = list(script), []

    async def health(self) -> ProviderHealth:
        return ProviderHealth(True, self.name, self.model, "TEST PROVIDER")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        step = self.script.pop(0)
        if isinstance(step, LLMResponse):
            return step
        calls = step if isinstance(step, list) else []
        return LLMResponse(step if isinstance(step, str) else "", calls, self.name, self.model, 0.0)
