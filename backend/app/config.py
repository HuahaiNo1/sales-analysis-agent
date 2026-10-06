from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = "postgresql://sales_app@127.0.0.1:55432/sales_agent"
    analytics_database_url: str = "postgresql://sales_reader@127.0.0.1:55432/sales_agent"
    import_database_url: str = "postgresql://sales_owner@127.0.0.1:55432/sales_agent"
    agent_mode: str = "mock"
    deepseek_api_key: SecretStr = SecretStr("")
    session_hours: int = 12
    result_hours: int = 24


settings = Settings()
