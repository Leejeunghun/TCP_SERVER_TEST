from __future__ import annotations

import argparse
import logging

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO

from server import TCPServer

app = Flask(__name__)
app.config["SECRET_KEY"] = "tcp-test-dashboard"
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

tcp_server: TCPServer | None = None


def handle_event(event: str, payload: dict) -> None:
    socketio.emit(event, payload)


@app.route("/")
def index():
    return render_template(
        "index.html",
        host=tcp_server.host,
        port=tcp_server.port,
        idle_timeout=tcp_server.idle_timeout,
    )


@app.route("/api/clients")
def api_clients():
    return jsonify(tcp_server.snapshot())


@app.route("/api/history")
def api_history():
    return jsonify(tcp_server.load_history())


@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.get_json(force=True) or {}
    client_id = data.get("client_id", "")
    text = data.get("text", "")
    ok = tcp_server.send_to_client(client_id, text.encode("utf-8"))
    return jsonify({"ok": ok})


@socketio.on("connect")
def on_socket_connect():
    socketio.emit("snapshot", tcp_server.snapshot())


def main():
    parser = argparse.ArgumentParser(description="TCP communication test server with a web dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="TCP listen host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=44202, help="TCP listen port (default: 44202)")
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=30.0,
        help="Seconds of inactivity before a connected client is flagged idle (default: 30)",
    )
    parser.add_argument("--web-host", default="0.0.0.0", help="Dashboard web host (default: 0.0.0.0)")
    parser.add_argument("--web-port", type=int, default=5000, help="Dashboard web port (default: 5000)")
    parser.add_argument(
        "--history-path",
        default="connection_history.log",
        help="Path to the persistent connect/disconnect history log (default: connection_history.log)",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Disable persistent connect/disconnect history logging",
    )
    args = parser.parse_args()

    global tcp_server
    tcp_server = TCPServer(
        host=args.host,
        port=args.port,
        idle_timeout=args.idle_timeout,
        on_event=handle_event,
        history_path=None if args.no_history else args.history_path,
    )
    tcp_server.start()

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    display_host = "localhost" if args.web_host == "0.0.0.0" else args.web_host
    print(f"[TCP] listening on {args.host}:{args.port} (idle timeout {args.idle_timeout}s)")
    print(f"[WEB] dashboard at http://{display_host}:{args.web_port}")
    socketio.run(app, host=args.web_host, port=args.web_port)


if __name__ == "__main__":
    main()
