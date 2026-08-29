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
    # Pool sizing — see P-1 rationale: 16/8 comfortably outlasts operator-paced
    # clicking (max ~24 concurrent) while staying below the 40-thread Starlette cap.
    sqlite_pool_size: int = 16
    sqlite_max_overflow: int = 8
    sqlite_pool_timeout_seconds: int = 30
    # Stage 4 intra-file collision path — Phase 18c §6.3 rollback affordance.
    # False (default): Stage 3.6 surfaces duplicates for review; Stage 4 skips
    #                  the collision block.
    # True:            Restores pre-Phase-18c behaviour (Stage 4 errors on
    #                  intra-file dupes) for rollback only.
    # DELETE this setting and the Stage 4 collision block in the release after
    # Phase 18c confirms no regression.
    intra_file_collision_legacy_error_path: bool = False
    known_part_numbers_chunk: int = 1000
    similar_cache_max_entries: int = 2048
    similar_cache_idle_ttl_seconds: float = 3600.0
    similar_cache_scan_every_n: int = 256
    gc_freeze_after_startup: bool = True
    missing_job_sweep_max_discards: int = 30
    export_chunk_rows: int = 500
    model_config = SettingsConfigDict(env_prefix="SCHEDULER_", env_file=".env", extra="ignore")

    def model_post_init(self, __ctx) -> None:
        if not self.database_url:
            object.__setattr__(self, "database_url", _default_database_url())

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
