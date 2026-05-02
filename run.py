import logging
import logging.handlers
import os
import sys
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config


def _runtime_root() -> Path:
    # Frozen: directory containing Scheduler.exe (writable user folder).
    # Dev:    repo root.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def _log_dir() -> Path:
    d = _runtime_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _configure_logging() -> None:
    handler = logging.handlers.RotatingFileHandler(
        _log_dir() / "scheduler.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler, logging.StreamHandler(sys.stdout)],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _alembic_paths() -> tuple[Path, Path]:
    """Return (script_location, sqlite_db_path) resolved for the current runtime."""
    if getattr(sys, "frozen", False):
        meipass = Path(sys._MEIPASS)
        exe_dir = Path(sys.executable).resolve().parent
        scripts_dir = meipass / "backend" / "alembic"
        db_path = exe_dir / "schedule.db"
    else:
        repo_root = Path(__file__).resolve().parent
        scripts_dir = repo_root / "backend" / "alembic"
        db_path = repo_root / "backend" / "outputs" / "db" / "schedule.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return scripts_dir, db_path


def _migrate() -> None:
    scripts_dir, db_path = _alembic_paths()
    sqlalchemy_url = f"sqlite:///{db_path.as_posix()}"

    cfg = Config()
    cfg.set_main_option("script_location", str(scripts_dir))
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url)

    # Belt-and-suspenders: also export the URL via env so any Settings consumer
    # (including alembic env.py's get_settings() fallback) resolves to the same DB.
    os.environ["SCHEDULER_DATABASE_URL"] = sqlalchemy_url

    logging.getLogger("scheduler.bootstrap").info(
        "Running Alembic upgrade head against %s (scripts=%s)", sqlalchemy_url, scripts_dir,
    )
    command.upgrade(cfg, "head")


def main() -> None:
    _configure_logging()
    _migrate()
    from backend.app.main import app   # late import: settings + engine resolve after migrate.
    port = int(os.environ.get("SCHEDULER_PORT", "8000"))
    uvicorn.run(
        app,
        host="0.0.0.0",      # MANDATORY — LAN access. 127.0.0.1 / localhost will not work.
        port=port,
        log_config=None,     # we own logging
        access_log=False,    # avoid stdout chatter when console is hidden
    )

if __name__ == "__main__":
    main()
