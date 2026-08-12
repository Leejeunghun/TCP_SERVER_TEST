from __future__ import annotations

import argparse
import socket
import time


def run(host: str, port: int, count: int, interval: float, hold: float) -> None:
    i = 0
    while count == 0 or i < count:
        i += 1
        try:
            with socket.create_connection((host, port), timeout=5) as sock:
                msg = f"TEST_MSG#{i} {time.strftime('%Y%m%d%H%M%S')}".encode()
                sock.sendall(msg)
                print(f"[{i}] sent {len(msg)}B, holding connection {hold}s")
                time.sleep(hold)
        except OSError as e:
            print(f"[{i}] connection failed: {e}")
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Simulate a client connecting/sending/disconnecting against the dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=44202)
    parser.add_argument("--count", type=int, default=0, help="number of connect cycles, 0 = infinite")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds to wait between connections")
    parser.add_argument("--hold", type=float, default=1.0, help="seconds to hold each connection open before closing")
    args = parser.parse_args()
    run(args.host, args.port, args.count, args.interval, args.hold)


if __name__ == "__main__":
    main()
