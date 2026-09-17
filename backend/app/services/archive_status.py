import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Literal, Optional, Union, get_args

from ..config import Settings

Clock = Callable[[], float]

SessionOutcome = Literal[
    "Completed", "AbandonedDisconnect", "AbandonedBudget",
    "AbandonedStall", "FailedFraming", "TicketRefused",
    "FailedStart", "FolderNotFound", "ListingUnavailable",
    "SourceChanged", "PreflightStalled"
]

RejectionReason = Literal[
    "TokenExpired", "TokenSpent", "TokenScope",
    "PermitsExhausted", "ReaderBacklog"
]

TerminalReason = Union[SessionOutcome, RejectionReason]
StatusState = Literal["Pending", "Preparing", "Streaming", "Terminal"]

SESSION_OUTCOMES = frozenset(get_args(SessionOutcome))
REJECTION_REASONS = frozenset(get_args(RejectionReason))
SECOND_REQUEST_OUTCOMES = REJECTION_REASONS | {"TicketRefused"}


@dataclass
class SessionStatus:
    state: StatusState
    outcome: Optional[TerminalReason]
    bytes_sent: int
    entry_count: int
    unresolved_count: int
    minted_loopback: bool
    recorded_at: float


_status_lock = threading.Lock()
_status_store: "OrderedDict[str, SessionStatus]" = OrderedDict()


def record_status(
    token: str,
    state: StatusState,
    outcome: Optional[TerminalReason],
    bytes_sent: int,
    entry_count: int,
    unresolved_count: int,
    minted_loopback: bool,
    settings: Settings,
    clock: Clock
) -> None:
    """Records one state transition against a token.

    An existing Terminal record is never overwritten by another terminal or
    streaming write. A rejection, or a lost bind race, never replaces a live
    session's Preparing or Streaming record: it belongs to a second request, and
    the session's own outcome must still land. Preparing is written only after
    a successful bind, so it starts the token's authoritative redemption and
    replaces whatever an earlier one left (Phase 32 §6.2).
    """
    with _status_lock:
        now = clock()
        existing = _status_store.get(token)
        if existing is not None:
            if existing.state == "Terminal" and state != "Preparing":
                return
            if outcome in SECOND_REQUEST_OUTCOMES and existing.state in ("Preparing", "Streaming"):
                return
            minted_loopback = existing.minted_loopback or minted_loopback

        _status_store[token] = SessionStatus(
            state=state,
            outcome=outcome,
            bytes_sent=bytes_sent,
            entry_count=entry_count,
            unresolved_count=unresolved_count,
            minted_loopback=minted_loopback,
            recorded_at=now
        )
        _status_store.move_to_end(token)

        if len(_status_store) > settings.shipping_photos_archive_status_max:
            limit_time = now - settings.shipping_photos_archive_token_ttl_seconds - settings.shipping_photos_archive_status_grace_seconds
            for key, status in list(_status_store.items()):
                if status.recorded_at < limit_time:
                    _status_store.pop(key)
                    break
            else:
                _status_store.popitem(last=False)


def get_status(token: str, settings: Settings, clock: Clock) -> Optional[SessionStatus]:
    with _status_lock:
        now = clock()
        status = _status_store.get(token)
        if status is None:
            return None
        ttl = settings.shipping_photos_archive_token_ttl_seconds
        grace = settings.shipping_photos_archive_status_grace_seconds
        if now > status.recorded_at + ttl + grace:
            return None
        return status


def clear_status() -> None:
    with _status_lock:
        _status_store.clear()
