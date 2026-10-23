from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    demo_mode: bool = True
    dev_console: bool = True
    database_url: str = ""
    document_root: str = "./data/documents"

    llm_default_route: str = "gemini"  # gemini | mac_dev | mac_quality | home_gpu | fixture_test
    llm_allow_fallback: bool = True
    llm_fallback_route: str = "mac_dev"
    matcher_max_tool_rounds: int = 10
    reviewer_max_tool_rounds: int = 6
    llm_context_tokens: int = 8192
    llm_temperature: float = 0.0
    llm_call_timeout_seconds: float = 120

    ollama_mac_url: str = "http://127.0.0.1:11434"
    ollama_mac_dev_model: str = "qwen3.5:4b"
    ollama_mac_quality_model: str = "qwen3.5:9b"
    ollama_home_url: str = "http://127.0.0.1:11435"
    ollama_home_model: str = "qwen3.5:9b"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"

    def get_gemini_api_key(self) -> str:
        if self.gemini_api_key:
            return self.gemini_api_key
        for path in (Path("API_Secret.txt"), Path("CreditBridge/API_Secret.txt"),
                     Path(__file__).resolve().parents[2] / "API_Secret.txt"):
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        return ""

    def route(self, name: str) -> tuple[str, str]:
        """Return (base_url, model) for a route name."""
        routes = {
            "mac_dev": (self.ollama_mac_url, self.ollama_mac_dev_model),
            "mac_quality": (self.ollama_mac_url, self.ollama_mac_quality_model),
            "home_gpu": (self.ollama_home_url, self.ollama_home_model),
            "gemini": ("gemini_api", self.gemini_model),
            "fixture_test": ("fake", "fake"),
        }
        return routes[name]


@lru_cache
def get_settings() -> Settings:
    return Settings()
