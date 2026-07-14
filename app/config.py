from pydantic_settings import BaseSettings
from pathlib import Path
from typing import List

class Settings(BaseSettings):
    master_username: str = "master"
    master_password: str = "changeme-master"
    viewer_username: str = "viewer"
    viewer_password: str = "changeme-viewer"
    jwt_secret: str = "your-super-secret-jwt-key"
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    login_rate_limit: str = "5/minute"

    # Paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    storage_dir: Path = base_dir / "storage"
    thumbnails_dir: Path = base_dir / "thumbnails"
    logs_dir: Path = base_dir / "logs"
    database_url: str = f"sqlite:///{base_dir / 'drive.db'}"

    @property
    def cors_origins(self) -> List[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
