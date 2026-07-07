"""Port availability utilities."""

import socket


def find_available_port(start_port: int = 8080, max_attempts: int = 100) -> int | None:
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return None
