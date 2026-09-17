"""Real-socket harness for archive liveness and protocol tests (Phase 31 §9, Phase 32 §13).

TestClient cannot express a peer that stops reading, so every liveness property
runs against a real uvicorn server on loopback.
"""
import http.client
import json
import logging
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import quote

import uvicorn

import backend.app.services.runtime_config as rc
from backend.app.api import create_app
from backend.app.config import Settings, get_settings
from backend.app.services.archive_status import clear_status, get_status
from backend.app.services.archive_tokens import clear_tickets
from backend.app.services.photo_files import _file_indexes

DATE_FOLDER = "2023_01_01"


def reset_archive_state(monkeypatch) -> None:
    monkeypatch.setattr(rc, "_cached_config", None)
    monkeypatch.setattr(rc, "load_runtime_config", lambda: {})
    _file_indexes.clear()
    clear_tickets()
    clear_status()


class LiveServer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.app = create_app()
        self.app.dependency_overrides[get_settings] = lambda: self.settings
        config = uvicorn.Config(self.app, host="127.0.0.1", port=0, lifespan="off", log_config=None, access_log=False)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="LiveServer")
        self.port = 0

    def start(self) -> "LiveServer":
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.01)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    @property
    def permits(self):
        return getattr(self.app.state, "archive_permits", None)

    def request(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 10.0):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), raw
        finally:
            connection.close()

    def mint(self, selection: Optional[List[str]] = None, date_folder: str = DATE_FOLDER) -> str:
        status, _, raw = self.request("POST", "/api/photos/archive-token", {"date_folder": date_folder, "selection": selection or []})
        assert status == 200, raw
        return json.loads(raw)["token"]

    def download_path(self, token: str) -> str:
        return f"/api/photos/archive-download?token={quote(token)}"


@dataclass
class DownloadResult:
    status: int
    headers: Dict[str, str]
    body: bytes = field(repr=False)

    @property
    def declared(self) -> Optional[int]:
        value = self.headers.get("content-length")
        return int(value) if value is not None else None

    @property
    def http_complete(self) -> bool:
        return self.declared is not None and len(self.body) == self.declared


class RawDownload:
    """One archive download over a raw socket, so the test controls reading."""

    def __init__(self, server: LiveServer, token: str, accept_encoding: str = "gzip, deflate, br",
                 receive_buffer: Optional[int] = None, method: str = "GET"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if receive_buffer is not None:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
        self.sock.connect(("127.0.0.1", server.port))
        request = (
            f"{method} {server.download_path(token)} HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\nAccept-Encoding: {accept_encoding}\r\nConnection: close\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        self.buffer = b""
        self.status = 0
        self.headers: Dict[str, str] = {}

    def read_head(self, timeout: float = 10.0) -> None:
        self.sock.settimeout(timeout)
        while b"\r\n\r\n" not in self.buffer:
            block = self.sock.recv(65536)
            if not block:
                raise ConnectionError("closed before headers")
            self.buffer += block
        head, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        self.status = int(lines[0].split()[1])
        self.headers = {k.strip().lower(): v.strip() for k, v in (line.split(":", 1) for line in lines[1:])}

    def read_some(self, count: int, timeout: float = 10.0) -> None:
        self.sock.settimeout(timeout)
        target = len(self.buffer) + count
        while len(self.buffer) < target:
            block = self.sock.recv(min(65536, target - len(self.buffer)))
            if not block:
                return
            self.buffer += block

    def read_all(self, rate_bytes_per_second: Optional[float] = None, timeout: float = 60.0) -> DownloadResult:
        self.sock.settimeout(timeout)
        started = time.monotonic()
        received_at_start = len(self.buffer)
        while True:
            try:
                block = self.sock.recv(16384 if rate_bytes_per_second else 1 << 20)
            except (ConnectionResetError, ConnectionAbortedError):
                break
            if not block:
                break
            self.buffer += block
            if rate_bytes_per_second:
                expected = (len(self.buffer) - received_at_start) / rate_bytes_per_second
                lag = expected - (time.monotonic() - started)
                if lag > 0:
                    time.sleep(lag)
        self.sock.close()
        return DownloadResult(self.status, self.headers, self.buffer)

    def reset(self) -> None:
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        self.sock.close()


def wait_for(predicate: Callable[[], bool], timeout: float, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def wait_terminal(token: str, settings: Settings, timeout: float = 15.0):
    wait_for(lambda: (s := get_status(token, settings, time.monotonic)) is not None and s.state == "Terminal", timeout)
    return get_status(token, settings, time.monotonic)


SESSION_RECORD = re.compile(r"ArchiveSessionRecord: session_id=(?P<session_id>\w+) .*?declared_bytes=(?P<declared>\S+) bytes_sent=(?P<sent>\d+).*?divergence_cause=(?P<cause>\S+).*?outcome=(?P<outcome>\w+)")


@dataclass
class SessionRecord:
    session_id: str
    declared_bytes: Optional[int]
    bytes_sent: int
    divergence_cause: Optional[str]
    outcome: str


def session_records(records: List[logging.LogRecord]) -> List[SessionRecord]:
    parsed = []
    for record in records:
        match = SESSION_RECORD.search(record.getMessage())
        if match:
            declared = match["declared"]
            parsed.append(SessionRecord(
                match["session_id"],
                None if declared == "None" else int(declared),
                int(match["sent"]),
                None if match["cause"] == "None" else match["cause"],
                match["outcome"],
            ))
    return parsed


class CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: List[logging.LogRecord] = []
        self.records_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        with self.records_lock:
            self.records.append(record)

    def snapshot(self) -> List[logging.LogRecord]:
        with self.records_lock:
            return list(self.records)
