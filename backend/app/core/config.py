from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RAG_")

    database_url: str = "postgresql+psycopg://rag:rag@localhost:5433/rag"
    storage_dir: Path = Path("storage")
    jwt_secret: str = "dev-secret-change-me"
    encryption_key: str = "dev-encryption-key-32-bytes-ok!!"  # 生产由 env 覆盖
    allowed_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
