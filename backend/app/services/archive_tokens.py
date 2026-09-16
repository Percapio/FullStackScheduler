from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Callable, List, Optional, Union

from ..config import Settings

from ..services.photo_files import SubFolder, ROOT

@dataclass(frozen=True)
class ArchiveTicket:
    """issued_at is stamped by issue_ticket under _lock. Callers construct a
    ticket without it and must not rely on the value they pass."""
    date_folder: str
    sub_folder: SubFolder
    selection: List[str]
    filename: str
    minted_loopback: bool
    issued_at: float = 0.0
    bound_session_id: Optional[str] = None
    spent: bool = False
    is_retry: bool = False

@dataclass(frozen=True)
class Admissible:
    ticket: ArchiveTicket

class Expired:
    pass

class Spent:
    pass

class ScopeViolation:
    pass

@dataclass(frozen=True)
class Bound:
    ticket: ArchiveTicket

@dataclass(frozen=True)
class Retry:
    ticket: ArchiveTicket

def archive_attachment_name(date_folder: str, sub_folder: SubFolder) -> str:
    import hashlib
    import re
    if sub_folder == ROOT:
        return f"Photos_{date_folder}.zip"
    
    reduced = re.sub(r'[^A-Za-z0-9_.-]', '', sub_folder)[:64]
    if reduced == sub_folder and reduced:
        slug = reduced
    else:
        digest = hashlib.sha256(sub_folder.encode('utf-8')).hexdigest()[:8]
        slug = f"x{digest}"
    
    return f"Photos_{date_folder}_{slug}.zip"

_lock = threading.Lock()
_tickets: "OrderedDict[str, ArchiveTicket]" = OrderedDict()

def _purge_expired(now: float, ttl: float) -> None:
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
    token = secrets.token_urlsafe(32)
    with _lock:
        now = clock()
        _purge_expired(now, settings.shipping_photos_archive_token_ttl_seconds)
        _tickets[token] = replace(ticket, issued_at=now)
        while len(_tickets) > settings.shipping_photos_archive_token_max:
            _tickets.popitem(last=False)
    return token

def inspect_ticket(
    token: str,
    is_loopback: bool,
    settings: Settings,
    clock: Callable[[], float],
) -> Union[Admissible, Expired, Spent, ScopeViolation]:
    now = clock()
    ttl = settings.shipping_photos_archive_token_ttl_seconds
    with _lock:
        _purge_expired(now, ttl)
        ticket = _tickets.get(token)
        if ticket is None or now - ticket.issued_at >= ttl:
            return Expired()
        if ticket.spent:
            return Spent()
        if ticket.minted_loopback and not is_loopback:
            return ScopeViolation()
        return Admissible(ticket)

def bind_ticket(
    token: str,
    session_id: str,
    settings: Settings,
    clock: Callable[[], float]
) -> Union[Bound, Retry, Expired, Spent]:
    now = clock()
    ttl = settings.shipping_photos_archive_token_ttl_seconds
    with _lock:
        _purge_expired(now, ttl)
        ticket = _tickets.get(token)
        if ticket is None or now - ticket.issued_at >= ttl:
            return Expired()
        if ticket.spent:
            return Spent()
        if ticket.bound_session_id is not None:
            return Spent()
        
        new_ticket = replace(ticket, bound_session_id=session_id)
        _tickets[token] = new_ticket
        if ticket.is_retry:
            return Retry(new_ticket)
        else:
            return Bound(new_ticket)

def settle_ticket(
    token: str,
    session_id: str,
    bytes_sent: int
) -> None:
    with _lock:
        ticket = _tickets.get(token)
        if ticket is None:
            return
        if ticket.bound_session_id != session_id:
            return
        if bytes_sent > 0:
            new_ticket = replace(ticket, spent=True)
        else:
            new_ticket = replace(ticket, bound_session_id=None, is_retry=True)
        _tickets[token] = new_ticket

def clear_tickets() -> None:
    with _lock:
        _tickets.clear()
