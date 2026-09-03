import asyncio
import queue
import logging
import dataclasses
from dataclasses import dataclass
from typing import Optional, Union, Literal

from starlette.websockets import WebSocket
from .config import Settings

logger = logging.getLogger(__name__)

ClientId = str
BatchId = int

@dataclass
class ScheduleMerged:
    batch_id: BatchId
    rows_inserted: int
    rows_updated: int
    origin: Optional[ClientId]
    type: Literal["schedule_merged"] = "schedule_merged"

@dataclass
class BatchAwaitingReview:
    batch_id: BatchId
    origin: Optional[ClientId]
    type: Literal["batch_awaiting_review"] = "batch_awaiting_review"

UpdateEvent = Union[ScheduleMerged, BatchAwaitingReview]

@dataclass
class Queued:
    depth_after: int

@dataclass
class DroppedQueueFull:
    event_type: str
    consecutive: int

PublishOutcome = Union[Queued, DroppedQueueFull]

class EventPublisher:
    def __init__(self, q: queue.Queue):
        self._q = q
        self._dropped_consecutive = 0

    def publish(self, event: UpdateEvent) -> PublishOutcome:
        try:
            self._q.put_nowait(event)
            self._dropped_consecutive = 0
            return Queued(depth_after=self._q.qsize())
        except queue.Full:
            self._dropped_consecutive += 1
            if self._dropped_consecutive == 1:
                logger.warning(f"WebSocket publish queue full; dropping {event.type} event. (Dropped {self._dropped_consecutive} consecutive)")
            return DroppedQueueFull(event_type=event.type, consecutive=self._dropped_consecutive)

@dataclass
class WebSocketConnection:
    socket: WebSocket
    client_id: Optional[ClientId]
    closing: bool = False

@dataclass
class FanOutTally:
    delivered: int
    skipped_origin: int
    evicted: int

class ScheduleUpdateHub:
    def __init__(self, settings: Settings):
        self._connections: set[WebSocketConnection] = set()
        self._settings = settings

    def register(self, connection: WebSocketConnection, client_id: Optional[ClientId]) -> Literal["Registered", "RejectedAtCapacity"]:
        if len(self._connections) >= self._settings.ws_max_connections:
            return "RejectedAtCapacity"
        connection.client_id = client_id
        self._connections.add(connection)
        return "Registered"

    def deregister(self, connection: WebSocketConnection) -> None:
        self._connections.discard(connection)

    async def fan_out(self, event: UpdateEvent) -> FanOutTally:
        payload = dataclasses.asdict(event)
        origin = payload.pop("origin", None)

        tasks = []
        conns_to_send = []
        skipped = 0

        for conn in self._connections:
            if conn.closing:
                continue
            if origin is not None and conn.client_id == origin:
                skipped += 1
                continue
            
            conns_to_send.append(conn)
            tasks.append(
                asyncio.wait_for(
                    conn.socket.send_json(payload),
                    timeout=self._settings.ws_send_timeout_seconds
                )
            )

        if not tasks:
            return FanOutTally(0, skipped, 0)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        delivered = 0
        evicted = 0

        for conn, res in zip(conns_to_send, results):
            if isinstance(res, Exception):
                conn.closing = True
                self.deregister(conn)
                # hub does not await teardown
                asyncio.create_task(conn.socket.close())
                evicted += 1
            else:
                delivered += 1

        return FanOutTally(delivered, skipped, evicted)

    async def close_all(self):
        for conn in list(self._connections):
            conn.closing = True
            try:
                await conn.socket.close()
            except Exception:
                pass
            self.deregister(conn)
