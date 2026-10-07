from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, overridable through env vars (prefix GEO_)."""

    model_config = SettingsConfigDict(env_prefix="GEO_")

    data_dir: Path = Path("data")
    max_upload_mb: int = 50
    max_unzipped_mb: int = 300
    max_zip_members: int = 500

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'app.db'}"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"


settings = Settings()
