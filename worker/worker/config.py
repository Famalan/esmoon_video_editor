from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_url: str = "redis://redis:6379/0"

    minio_endpoint: str = "http://minio:9000"
    minio_root_user: str = "minioadmin"
    minio_root_password: str = "minioadmin"
    minio_bucket: str = "video-slicer"

    codex_cli_path: str = "/Applications/ChatGPT.app/Contents/Resources/codex"
    codex_model: str = "gpt-5.6-luna"
    codex_reasoning_effort: Literal[
        "none", "low", "medium", "high", "xhigh", "max"
    ] = "max"
    codex_timeout_sec: float = 1800.0

    whisper_model: str = "base"
    whisper_cache_dir: str = "../.cache/whisper"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
