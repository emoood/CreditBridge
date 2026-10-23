from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    demo_mode: bool = True
    dev_console: bool = True
    database_url: str = ""  # curriculum database (Supabase), read-only
    app_database_url: str = "postgresql+psycopg:///creditbridge"  # this app's own state: local PostgreSQL, never Supabase
    document_root: str = "./data/documents"
    demo_stage_seconds: float = 0.5  # Demo mode, speed "fast": about this long per stage ("instant" = 0)
    recordings_root: str = "./data/recordings"  # recorded real runs for replay (local, gitignored)
    catalog: str = "demo"  # demo (synthetic, used by fixtures/tests) | aud_2026_2027 (real AUD catalog, descriptions only)

    llm_default_route: str = "mac_dev"  # mac_dev | mac_quality | home_gpu | gemini | fixture_test
    llm_allow_fallback: bool = True
    llm_fallback_route: str = "mac_dev"
    matcher_max_tool_rounds: int = 6
    title_only_suggestions: bool = True  # for subjects with no syllabus: up to 3 candidate courses from the title, marked unverified
    reviewer_max_tool_rounds: int = 6
    case_max_tool_rounds: int = 3  # the Case agent is quick by design (read the mapping, act, submit)
    case_review: bool = True  # the Case agent also reviews the whole application (mapped rows included) after the rows are worked
    case_max_seconds: float = 90  # budget per row run; once rounds or seconds are used up the agent may only submit
    case_review_max_tool_rounds: int = 4  # the whole-application review (list rows, raise doubts, judge borderline electives, summary)
    case_review_max_seconds: float = 90
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
    intake_agent: bool = True  # the Intake agent drives the reading of every upload; False = code reads every block, no planning or recovery
    vision_reads_digital: bool = True  # digital PDFs are read by vision too; their text layer is only a cross-check
    intake_max_tool_rounds: int = 14  # the whole reading plan: list pages, read blocks, check, recover, submit
    intake_max_seconds: float = 400
    intake_recovery_rounds: int = 2  # recovery re-reads per transcript (tighter crop, higher zoom)
    auditor_max_tool_rounds: int = 6
    auditor_max_seconds: float = 90

    ollama_mac_url: str = "http://127.0.0.1:11434"
    ollama_mac_dev_model: str = "qwen3.5:4b"
    ollama_mac_quality_model: str = "qwen3.5:9b"
    ollama_home_url: str = "http://127.0.0.1:11435"
    ollama_home_model: str = "qwen3.5:9b"
    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_model: str = ""  # the identifier LM Studio shows for the loaded model

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_rpm: int = 15

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
            "lmstudio": (self.lmstudio_base_url, self.lmstudio_model),
            "gemini": ("gemini_api", self.gemini_model),
            "fixture_test": ("fake", "fake"),
        }
        return routes[name]


@lru_cache
def get_settings() -> Settings:
    return Settings()
