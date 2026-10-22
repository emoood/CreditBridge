from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    demo_mode: bool = True
    dev_console: bool = True
    database_url: str = ""
    document_root: str = "./data/documents"

    llm_default_route: str = "mac_dev"  # mac_dev | mac_quality | home_gpu | fixture_test
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
    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_model: str = ""  # the identifier LM Studio shows for the loaded model

    def route(self, name: str) -> tuple[str, str]:
        """Return (base_url, model) for a route name."""
        return {
            "mac_dev": (self.ollama_mac_url, self.ollama_mac_dev_model),
            "mac_quality": (self.ollama_mac_url, self.ollama_mac_quality_model),
            "home_gpu": (self.ollama_home_url, self.ollama_home_model),
            "lmstudio": (self.lmstudio_base_url, self.lmstudio_model),
        }[name]


@lru_cache
def get_settings() -> Settings:
    return Settings()
