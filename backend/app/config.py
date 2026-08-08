from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_secret_key: str = "change-me"

    database_url: str = "postgresql://werkstatt:change-me@postgres:5432/werkstatt_brain"

    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_vector_size: int = 1024  # bge-m3 (SPEC Kap. 2.2)

    redis_url: str = "redis://redis:6379/0"

    # --- Frontend (SPEC Kap. 5) ---
    frontend_origin: str = "http://localhost:5173"

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    cursor_api_key: str = ""

    # --- Agenten-System (SPEC Kap. 3) ---
    sandbox_script_path: str = "/app/sandbox/run.py"
    sandbox_timeout_seconds: int = 20
    agent_max_iterations: int = 6
    agent_llm_model: str = "claude-3-5-sonnet-20241022"
    agent_cursor_model: str = "composer-2.5"

    # --- Sandbox Execution Service (SPEC Kap. 1.6, 4) ---
    sandbox_docker_image: str = "werkstatt-sandbox:latest"
    sandbox_workspace_dir: str = "/app/sandbox_workspace"
    exports_dir: str = "/app/exports"
    sandbox_memory_limit: str = "512m"
    sandbox_cpu_limit: float = 1.0
    api_container_name: str = "werkstatt-api"

    # --- Asset Crawler & Vision Ingestion (SPEC Kap. 2.3) ---
    crawler_scan_paths: str = "/data/crawler_input"  # Komma-separierte Liste
    crawler_file_extensions: str = (
        ".step,.stp,.stl,.f3d,.png,.jpg,.jpeg,.webp,.gif,.bmp,.pdf,"
        ".txt,.md,.csv,.json,.xml,.svg,.dxf,.iges,.igs,.obj,.3mf"
    )
    # Bewusst AUSSERHALB von /app: uvicorn --reload beobachtet nur /app/app und
    # /app/agents (siehe backend/Dockerfile), aber ein Pfad unter /data ist
    # zusätzlich robust, falls der Watcher-Scope sich mal ändert.
    uploads_dir: str = "/data/uploads"
    vision_model: str = "claude-3-5-sonnet-20241022"
    openai_vision_model: str = "gpt-4o-mini"

    # --- Meta-Coach (SPEC Kap. 2.5) ---
    meta_coach_rules_dir: str = "/app/rules"
    meta_coach_scheduler_enabled: bool = False
    meta_coach_nightly_hour: int = 2

    # --- Chat-Logs & persistente Unterhaltungen ---
    agent_logs_dir: str = "/data/agent_logs"
    conversations_dir: str = "/data/conversations"

    @property
    def crawler_scan_path_list(self) -> list[str]:
        return [p.strip() for p in self.crawler_scan_paths.split(",") if p.strip()]

    @property
    def crawler_file_extension_set(self) -> set[str]:
        return {ext.strip().lower() for ext in self.crawler_file_extensions.split(",") if ext.strip()}


settings = Settings()
