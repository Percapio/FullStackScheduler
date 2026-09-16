import time
from typing import Literal, Optional, Union
from collections import OrderedDict
from dataclasses import dataclass
from ..config import Settings

SessionOutcome = Literal[
    "Completed", "AbandonedDisconnect", "AbandonedBudget",
    "AbandonedStall", "FailedFraming", "TicketRefused",
    "FailedStart"
]

RejectionReason = Literal[
    "TokenExpired", "TokenSpent", "TokenScope", "FolderNotFound",
    "ListingUnavailable", "PermitsExhausted", "ReaderBacklog"
]

TerminalReason = Union[SessionOutcome, RejectionReason]
StatusState = Literal["Pending", "Streaming", "Terminal"]

@dataclass
class SessionStatus:
    state: StatusState
    outcome: Optional[TerminalReason]
    bytes_sent: int
    entry_count: int
    unresolved_count: int
    minted_loopback: bool
    recorded_at: float

import threading
_status_lock = threading.Lock()
_status_store: OrderedDict[str, SessionStatus] = OrderedDict()

def record_status(
    token: str,
    state: StatusState,
    outcome: Optional[TerminalReason],
    bytes_sent: int,
    entry_count: int,
    unresolved_count: int,
    minted_loopback: bool,
    settings: Settings,
    clock: float
) -> None:
    with _status_lock:
        if token in _status_store:
            existing = _status_store[token]
            if existing.state == "Terminal":
                return
            if state == "Streaming" and existing.state == "Terminal":
                return
            minted_loopback = existing.minted_loopback

        _status_store[token] = SessionStatus(
            state=state,
            outcome=outcome,
            bytes_sent=bytes_sent,
            entry_count=entry_count,
            unresolved_count=unresolved_count,
            minted_loopback=minted_loopback,
            recorded_at=clock
        )
        _status_store.move_to_end(token)

        # Evict LRU
        if len(_status_store) > settings.shipping_photos_archive_status_max:
            # We don't sweep on read, but we can do a lazy sweep on insert
            ttl = settings.shipping_photos_archive_token_ttl_seconds
            grace = settings.shipping_photos_archive_status_grace_seconds
            limit_time = clock - ttl - grace
            
            # Find an expired one to drop
            dropped = False
            for k, v in list(_status_store.items()):
                if v.recorded_at < limit_time:
                    _status_store.pop(k)
                    dropped = True
                    break
                    
            if not dropped:
                # Evict least recently written
                _status_store.popitem(last=False)

def get_status(token: str, settings: Settings, clock: float) -> Optional[SessionStatus]:
    with _status_lock:
        if token not in _status_store:
            return None
        
        status = _status_store[token]
        ttl = settings.shipping_photos_archive_token_ttl_seconds
        grace = settings.shipping_photos_archive_status_grace_seconds
        
        if clock > status.recorded_at + ttl + grace:
            return None
            
        return status
