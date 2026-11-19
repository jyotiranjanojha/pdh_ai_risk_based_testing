from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=str(_ENV_PATH), env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Enterprise AI Risk-Based Testing (RBT) Platform"
    APP_VERSION: str = "2.0.0"

    # Dual-Mode Execution
    SNOWFLAKE_MOCK_MODE: bool = True

    # Snowflake Connection (used when SNOWFLAKE_MOCK_MODE=false)
    SNOWFLAKE_ACCOUNT: str = ""
    SNOWFLAKE_USER: str = ""
    SNOWFLAKE_PASSWORD: str = ""
    SNOWFLAKE_PRIVATE_KEY_PATH: str = ""
    SNOWFLAKE_PRIVATE_KEY_PASSPHRASE: str = ""
    SNOWFLAKE_AUTHENTICATOR: str = ""  # e.g., 'externalbrowser' or 'oauth'
    SNOWFLAKE_ROLE: str = "ROLE_IF_CDA_PDH_SUPPLY_PLANNING_ANALYSIS_CONTRIBUTOR"
    SNOWFLAKE_DATABASE: str = "IF_SUPPLY_CHAIN"
    SNOWFLAKE_SCHEMA: str = "PDH_SUPPLY_PLANNING_SRC_BASE"
    SNOWFLAKE_WAREHOUSE: str = "IF_WH_SCHAIN_CONSUMPTION"

    # Cortex AI & Execution Guardrails
    CORTEX_MODEL: str = "claude-sonnet-4-5"
    AVAILABLE_CORTEX_MODELS: list[str] = [
        "claude-sonnet-4-5",
        "llama3.3-70b",
        "llama3.1-70b",
        "mistral-large2",
    ]
    MAX_AUTO_DEBUG_RETRIES: int = 3
    STATEMENT_TIMEOUT_IN_SECONDS: int = 300
    MAX_CONCURRENT_QUERIES: int = 5
    SYNC_HTTP_TIMEOUT_SECONDS: int = 90
    MAX_EXPORT_CSV_ROWS: int = 1000

    # OIDC / JWT Auth (used when SNOWFLAKE_MOCK_MODE=false)
    ALLOW_DEMO_AUTH_HEADERS: bool = False
    OIDC_JWT_SECRET: str = "change-me-in-production"
    OIDC_JWT_ALGORITHM: str = "HS256"
    OIDC_AUDIENCE: str = "rbt-platform"

    # Mock SQLite persistence path (empty string = in-memory or default local file)
    MOCK_DB_PATH: str = ""

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"


settings = Settings()
