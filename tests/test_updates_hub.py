import asyncio

from fastapi.testclient import TestClient

from backend.app.api import create_app
from backend.app.config import Settings
from backend.app.updates import ScheduleMerged, ScheduleUpdateHub, WebSocketConnection


class FakeSocket:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent = []
        self.closed = False

    async def send_json(self, payload):
        if self.fail:
            raise ConnectionError("gone")
        self.sent.append(payload)

    async def close(self):
        self.closed = True


def test_connections_register_and_deregister_by_identity():
    hub = ScheduleUpdateHub(Settings())
    first = WebSocketConnection(socket=FakeSocket(), client_id=None)
    second = WebSocketConnection(socket=FakeSocket(), client_id=None)

    assert hub.register(first, "a") == "Registered"
    assert hub.register(second, "a") == "Registered"
    assert len(hub._connections) == 2

    first.closing = True
    hub.deregister(first)
    assert hub._connections == {second}


def test_register_refuses_past_capacity():
    hub = ScheduleUpdateHub(Settings(ws_max_connections=1))
    assert hub.register(WebSocketConnection(socket=FakeSocket(), client_id=None), "a") == "Registered"
    assert hub.register(WebSocketConnection(socket=FakeSocket(), client_id=None), "b") == "RejectedAtCapacity"


def test_fan_out_skips_origin_and_evicts_failed_sockets():
    async def scenario():
        hub = ScheduleUpdateHub(Settings())
        origin = WebSocketConnection(socket=FakeSocket(), client_id=None)
        healthy = WebSocketConnection(socket=FakeSocket(), client_id=None)
        broken = WebSocketConnection(socket=FakeSocket(fail=True), client_id=None)
        hub.register(origin, "origin")
        hub.register(healthy, "healthy")
        hub.register(broken, "broken")

        tally = await hub.fan_out(ScheduleMerged(batch_id=1, rows_inserted=2, rows_updated=3, origin="origin"))
        await asyncio.sleep(0)
        return hub, origin, healthy, broken, tally

    hub, origin, healthy, broken, tally = asyncio.run(scenario())
    assert (tally.delivered, tally.skipped_origin, tally.evicted) == (1, 1, 1)
    assert healthy.socket.sent == [{"batch_id": 1, "rows_inserted": 2, "rows_updated": 3, "type": "schedule_merged"}]
    assert origin.socket.sent == []
    assert broken not in hub._connections and broken.socket.closed


def test_websocket_endpoint_accepts_and_registers():
    app = create_app()
    hub = ScheduleUpdateHub(Settings())
    app.state.hub = hub
    client = TestClient(app)
    with client.websocket_connect("/api/ws/updates?client_id=abc") as socket:
        socket.send_text("ping")
        assert [c.client_id for c in hub._connections] == ["abc"]
