"""Debug router — backend log file viewer support.

The backend mirrors all Python logging output into a rotating file
(``logs/backend.log``, see ``paths.get_backend_log_file``) so the console
Debug page can inspect it regardless of how the process was started.

Endpoints
---------
* ``GET /api/debug/backend-logs?lines=200`` — tail of the backend log file
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import APIRouter, Query

from agentcore.runtime import paths

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/debug", tags=["debug"])

# Upper bound on lines the debug page may request in one call.
_MAX_DEBUG_LOG_LINES = 1000

# Mirror at most ~512KB of trailing file content per request.
_TAIL_MAX_BYTES = 512 * 1024

# Rotate at 2MB per file, keep 3 backups.
_ROTATE_MAX_BYTES = 2 * 1024 * 1024
_ROTATE_BACKUP_COUNT = 3

# Marker used to avoid attaching duplicate handlers on repeated calls
# (e.g. tests that rebuild the app, uvicorn --reload).
_HANDLER_ATTR = "_agentcore_file_handler"


# ---------------------------------------------------------------------------
# File logging setup
# ---------------------------------------------------------------------------


def setup_file_logging() -> Path:
    """Attach a rotating file handler to the root logger (idempotent).

    Also mirrors the uvicorn loggers (which do not propagate to root by
    default) so access/error lines land in the same file.  Returns the
    log file path.  Failures are logged but never block startup.
    """
    log_path = paths.get_backend_log_file()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        if getattr(root, _HANDLER_ATTR, False):
            return log_path

        handler = RotatingFileHandler(
            log_path,
            maxBytes=_ROTATE_MAX_BYTES,
            backupCount=_ROTATE_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
            )
        )
        handler.setLevel(logging.DEBUG)

        root.addHandler(handler)
        setattr(root, _HANDLER_ATTR, True)

        # Root defaults to WARNING — raise it so INFO application logs
        # reach the file (never lower an explicitly configured level).
        if root.level > logging.INFO:
            root.setLevel(logging.INFO)

        # uvicorn configures its own loggers with propagate=False — mirror
        # the handler onto them so server noise also reaches the file.
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(name).addHandler(handler)
    except Exception:  # pragma: no cover — defensive, never block startup
        logger.exception("Failed to set up backend file logging")
    return log_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tail_text_file(path: Path, *, lines: int) -> str:
    """Read the last *lines* lines of a text file with bounded memory."""
    if not path.is_file():
        return ""
    try:
        size = path.stat().st_size
        if size == 0:
            return ""
        with open(path, "rb") as f:
            if size <= _TAIL_MAX_BYTES:
                data = f.read()
            else:
                f.seek(max(size - _TAIL_MAX_BYTES, 0))
                data = f.read()
        text = data.decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except Exception:
        logger.exception("Failed to read backend log file")
        return ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/backend-logs",
    summary="Read backend log file tail for the debug page",
)
def get_backend_logs(
    lines: int = Query(
        200,
        ge=20,
        le=_MAX_DEBUG_LOG_LINES,
        description="Number of trailing log lines to return",
    ),
) -> dict[str, object]:
    """Return metadata plus the tail of ``logs/backend.log``."""
    log_path = paths.get_backend_log_file().resolve()
    try:
        st = log_path.stat()
        return {
            "path": str(log_path),
            "exists": True,
            "lines": lines,
            "updated_at": st.st_mtime,
            "size": st.st_size,
            "content": _tail_text_file(log_path, lines=lines),
        }
    except OSError:
        return {
            "path": str(log_path),
            "exists": False,
            "lines": lines,
            "updated_at": None,
            "size": 0,
            "content": "",
        }
