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
    # 2nd OPS bounds (Phase 22). Server-owned: the record read echoes the first
    # two to the client so no frontend constant can disagree with them.
    second_ops_max_lines: int = 500          # lines per job; B142006 AUDIT BOM has 56
    second_ops_note_max_chars: int = 4000    # the unexpected-inclusions note
    second_ops_preview_lines: int = 3        # lines rendered in the grid cell
    
    # Phase 25 — Shipping Photos
    shipping_photos_dir: str = ""
    shipping_photos_index_ttl_seconds: float = 7200.0
    shipping_photos_unavailable_ttl_seconds: float = 60.0
    shipping_photos_probe_max: int = 25
    shipping_photos_open_min_interval_seconds: float = 2.0
    shipping_photos_max_folders: int = 4000
    settings_browse_max_entries: int = 2000
    settings_browse_max_concurrent: int = 2
    
    # Phase 26 — Web Photo Gallery
    shipping_photos_file_index_ttl_seconds: float = 120.0
    shipping_photos_file_unavailable_ttl_seconds: float = 30.0
    shipping_photos_file_index_max_keys: int = 96
    shipping_photos_max_files_per_folder: int = 2000
    shipping_photos_max_subfolders_per_date: int = 200
    shipping_photos_thumb_max_edge_px: int = 400
    shipping_photos_thumb_quality: int = 78
    shipping_photos_thumb_cache_max_bytes: int = 512_000_000
    shipping_photos_thumb_sweep_every_n_writes: int = 256
    shipping_photos_thumb_max_concurrent: int = 8
    shipping_photos_thumb_queue_wait_seconds: float = 20.0
    shipping_photos_thumb_max_waiters: int = 16
    shipping_photos_thumb_warm_enabled: bool = True
    shipping_photos_thumb_warm_queue_max_keys: int = 24
    shipping_photos_thumb_warm_max_subfolders_per_date: int = 4
    shipping_photos_thumb_warm_backoff_seconds: float = 0.25
    shipping_photos_thumb_warm_max_attempts: int = 8
    shipping_photos_archive_max_concurrent: int = 2
    shipping_photos_archive_lan_max_files: int = 60
    shipping_photos_archive_lan_max_bytes: int = 750_000_000
    
    # Touch-Up 27 — Archive handoff
    shipping_photos_archive_token_ttl_seconds: float = 300.0
    shipping_photos_archive_token_max: int = 32
    # Touch-Up 26 — Update Channel
    ws_max_connections: int = 32
    ws_publish_queue_max: int = 64
    ws_send_timeout_seconds: float = 5.0
    ws_heartbeat_seconds: float = 30.0
    ws_drain_restart_backoff_seconds: float = 10.0
    
    model_config = SettingsConfigDict(env_prefix="SCHEDULER_", env_file=".env", extra="ignore")

    def model_post_init(self, __ctx) -> None:
        if not self.database_url:
            object.__setattr__(self, "database_url", _default_database_url())

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
