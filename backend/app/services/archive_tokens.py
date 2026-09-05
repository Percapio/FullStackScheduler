from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Callable, List, Optional

from ..config import Settings


@dataclass(frozen=True)
class ArchiveTicket:
    """issued_at is stamped by issue_ticket under _lock. Callers construct a
    ticket without it and must not rely on the value they pass."""
    date_folder: str
    selection: List[str]
    filename: str
    minted_loopback: bool
    issued_at: float = 0.0


_lock = threading.Lock()
_tickets: "OrderedDict[str, ArchiveTicket]" = OrderedDict()


def _purge_expired(now: float, ttl: float) -> None:
    """Evicts every ticket older than ttl, stopping at the first survivor.
    The early exit is sound only because issued_at is stamped under _lock by
    issue_ticket, which makes insertion order non-decreasing in issued_at.
    pre:  caller holds _lock
    post: no ticket with now - issued_at >= ttl remains"""
    while _tickets:
        _, ticket = next(iter(_tickets.items()))
        if now - ticket.issued_at >= ttl:
            _tickets.popitem(last=False)
        else:
            break


def issue_ticket(
    ticket: ArchiveTicket,
    settings: Settings,
    clock: Callable[[], float],
) -> str:
    """Stores ticket under a fresh opaque token.
    post: the stored ticket carries issued_at == the clock reading taken
          inside _lock, so _tickets stays ordered by issue time
    
    Note: This store assumes one worker process, exactly as the existing 
    _file_indexes and _archive_semaphore globals already do. Uvicorn must 
    continue to run with workers=1."""
    token = secrets.token_urlsafe(32)
    with _lock:
        now = clock()
        _purge_expired(now, settings.shipping_photos_archive_token_ttl_seconds)
        _tickets[token] = replace(ticket, issued_at=now)
        while len(_tickets) > settings.shipping_photos_archive_token_max:
            _tickets.popitem(last=False)
    return token


def redeem_ticket(
    token: str,
    settings: Settings,
    clock: Callable[[], float],
) -> Optional[ArchiveTicket]:
    """Look up a ticket WITHOUT consuming it."""
    now = clock()
    ttl = settings.shipping_photos_archive_token_ttl_seconds
    with _lock:
        _purge_expired(now, ttl)
        ticket = _tickets.get(token)
        if ticket is None or now - ticket.issued_at >= ttl:
            _tickets.pop(token, None)
            return None
        return ticket


def clear_tickets() -> None:
    """Test hook, mirroring photo_files._file_indexes.clear() usage in tests."""
    with _lock:
        _tickets.clear()
