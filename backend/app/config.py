from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    demo_mode: bool = True
    dev_console: bool = True
    database_url: str = ""  # curriculum database (Supabase), read-only
    app_database_url: str = "postgresql+psycopg:///creditbridge"  # this app's own state: local PostgreSQL, never Supabase
    document_root: str = "./data/documents"
    recordings_root: str = "./data/recordings"  # recorded real runs for replay (local, gitignored)
    catalog: str = "demo"  # demo (synthetic, used by fixtures/tests) | aud_2026_2027 (real AUD catalog, descriptions only)

    llm_default_route: str = "mac_dev"  # mac_dev | mac_quality | home_gpu | fixture_test
    llm_allow_fallback: bool = True
    llm_fallback_route: str = "mac_dev"
    matcher_max_tool_rounds: int = 6
    title_only_suggestions: bool = True  # for subjects with no syllabus: up to 3 candidate courses from the title, marked unverified
    reviewer_max_tool_rounds: int = 6
    case_max_tool_rounds: int = 3  # the Case agent is quick by design (read the mapping, act, submit)
    case_agent: bool = True  # the code-first path (gate, mapping lookup, Case agent). False = the older model evaluation of every subject
    llm_context_tokens: int = 8192
    agent_protocol: str = "native"  # native (runtime tool calls) | json_action (constrained JSON actions parsed by our code)
    llm_temperature: float = 0.0
    llm_call_timeout_seconds: float = 120
    ollama_keep_alive: str = "24h"  # keep the model resident between calls
    llm_think: bool = False  # thinking mode off: far fewer output tokens per call on the local model
    warmup_on_start: bool = True  # preload the model and send one warm-up call when the API starts
    vision_dpi: int = 200
    vision_reread_dpi: int = 300
    vision_max_block_px: int = 560
    vision_recheck_dpi: int = 400  # the Intake agent's focused re-read of one block
    intake_agent: bool = True
    intake_max_rechecks: int = 3  # re-reads per transcript
    intake_max_tool_rounds: int = 5
    auditor_max_tool_rounds: int = 6

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
