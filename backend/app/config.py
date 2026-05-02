import os
import sys
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

def _runtime_root() -> Path:
    # Frozen: folder containing the .exe (writable, persistent).
    # Dev:    repo root (parent of `backend/`).
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]

def _default_database_url() -> str:
    if getattr(sys, "frozen", False):
        # Beside the .exe -> persists across launches.
        db_path = _runtime_root() / "schedule.db"
    else:
        # Preserve existing dev layout.
        db_path = _runtime_root() / "backend" / "outputs" / "db" / "schedule.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # SQLAlchemy needs forward slashes even on Windows.
    return f"sqlite:///{db_path.as_posix()}"

class Settings(BaseSettings):
    database_url: str = ""             # filled by validator below
    sqlite_pragmas: bool = True
    model_config = SettingsConfigDict(env_prefix="SCHEDULER_", env_file=".env", extra="ignore")

    def model_post_init(self, __ctx) -> None:
        if not self.database_url:
            object.__setattr__(self, "database_url", _default_database_url())

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
