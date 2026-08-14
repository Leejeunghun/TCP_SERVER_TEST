from __future__ import annotations

import itertools
import json
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


@dataclass
class ClientInfo:
    client_id: str
    ip: str
    port: int
    connect_time: str
    last_seen: float
    status: str = "connected"  # connected | idle | disconnected
    bytes_recv: int = 0
    bytes_sent: int = 0
    recv_count: int = 0
    disconnect_time: Optional[str] = None
    log: deque = field(default_factory=lambda: deque(maxlen=50))

    def to_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "ip": self.ip,
            "port": self.port,
            "connect_time": self.connect_time,
            "disconnect_time": self.disconnect_time,
            "status": self.status,
            "last_seen_epoch": self.last_seen,
            "bytes_recv": self.bytes_recv,
            "bytes_sent": self.bytes_sent,
            "recv_count": self.recv_count,
            "log": list(self.log),
        }


class TCPServer:
    """Generic TCP monitor: accepts any client, tracks connect/idle/disconnect state."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 44202,
        idle_timeout: float = 30.0,
        on_event: Optional[Callable[[str, dict], None]] = None,
        history_path: Optional[str] = "connection_history.log",
    ):
        self.host = host
        self.port = port
        self.idle_timeout = idle_timeout
        self.on_event = on_event or (lambda event, payload: None)
        self.history_path = history_path

        self._sock: Optional[socket.socket] = None
        self._clients: dict[str, ClientInfo] = {}
        self._client_sockets: dict[str, socket.socket] = {}
        self._lock = threading.Lock()
        self._history_lock = threading.Lock()
        self._seq = itertools.count(1)
        self._running = False
        self.start_time: Optional[str] = None

    # -- lifecycle --
    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(20)
        self._running = True
        self.start_time = now_iso()
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._idle_monitor_loop, daemon=True).start()
        self._emit("server_started", {"host": self.host, "port": self.port, "time": self.start_time})

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        with self._lock:
            sockets = list(self._client_sockets.values())
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass

    # -- queries / actions --
    def snapshot(self) -> list[dict]:
        with self._lock:
            return [c.to_dict() for c in sorted(self._clients.values(), key=lambda c: c.connect_time)]

    def load_history(self, limit: int = 500) -> list[dict]:
        """Read persisted connect/disconnect records, most recent last. Survives server restarts."""
        if not self.history_path:
            return []
        try:
            with self._history_lock, open(self.history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def send_to_client(self, client_id: str, data: bytes) -> bool:
        with self._lock:
            sock = self._client_sockets.get(client_id)
            client = self._clients.get(client_id)
        if not sock or not client or client.status == "disconnected":
            return False
        try:
            sock.sendall(data)
        except OSError:
            return False
        with self._lock:
            client.bytes_sent += len(data)
            client.log.append({"t": now_iso(), "dir": "SEND", "preview": self._preview(data)})
            snapshot = client.to_dict()
        self._emit("client_update", snapshot)
        return True

    # -- internal loops --
    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, addr = self._sock.accept()
            except OSError:
                break
            client_id = f"{addr[0]}:{addr[1]}#{next(self._seq)}"
            info = ClientInfo(
                client_id=client_id,
                ip=addr[0],
                port=addr[1],
                connect_time=now_iso(),
                last_seen=time.time(),
            )
            with self._lock:
                self._clients[client_id] = info
                self._client_sockets[client_id] = conn
            self._emit("client_connected", info.to_dict())
            threading.Thread(target=self._client_loop, args=(client_id, conn), daemon=True).start()

    def _client_loop(self, client_id: str, conn: socket.socket) -> None:
        conn.settimeout(1.0)
        while self._running:
            try:
                data = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            with self._lock:
                client = self._clients[client_id]
                client.last_seen = time.time()
                client.bytes_recv += len(data)
                client.recv_count += 1
                if client.status == "idle":
                    client.status = "connected"
                client.log.append({"t": now_iso(), "dir": "RECV", "preview": self._preview(data)})
                snapshot = client.to_dict()
            self._emit("client_update", snapshot)
        self._mark_disconnected(client_id)

    def _mark_disconnected(self, client_id: str) -> None:
        with self._lock:
            client = self._clients.get(client_id)
            if not client or client.status == "disconnected":
                return
            client.status = "disconnected"
            client.disconnect_time = now_iso()
            sock = self._client_sockets.pop(client_id, None)
            snapshot = client.to_dict()
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        self._emit("client_disconnected", snapshot)

    def _idle_monitor_loop(self) -> None:
        while self._running:
            time.sleep(1)
            stale = []
            with self._lock:
                for client in self._clients.values():
                    if client.status == "connected" and time.time() - client.last_seen > self.idle_timeout:
                        client.status = "idle"
                        stale.append(client.to_dict())
            for snap in stale:
                self._emit("client_idle", snap)

    @staticmethod
    def _preview(data: bytes, limit: int = 64) -> dict:
        chunk = data[:limit]
        hex_str = " ".join(f"{b:02x}" for b in chunk)
        text = chunk.decode("utf-8", errors="replace")
        return {"hex": hex_str, "text": text, "len": len(data), "truncated": len(data) > limit}

    def _emit(self, event: str, payload: dict) -> None:
        if event in ("client_connected", "client_disconnected"):
            self._append_history(event, payload)
        try:
            self.on_event(event, payload)
        except Exception:
            pass

    def _append_history(self, event: str, payload: dict) -> None:
        if not self.history_path:
            return
        record = {
            "time": now_iso(),
            "event": event,
            "client_id": payload.get("client_id"),
            "ip": payload.get("ip"),
            "port": payload.get("port"),
            "connect_time": payload.get("connect_time"),
            "disconnect_time": payload.get("disconnect_time"),
        }
        try:
            with self._history_lock, open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
