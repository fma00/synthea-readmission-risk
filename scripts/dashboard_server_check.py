"""Server-level checks of the dashboard (v3 slice 4, Interfaces §4 in
notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md; slice 3's S15). Against a local container (CI job dashboard-image,
`make dashboard-image-check`) or the live service (`deploy.yml`'s smoke, `make dashboard-server-check`):

- SC1 GET /_stcore/health: 200, body `ok`, `Cache-Control: no-cache`, an `_streamlit_xsrf` cookie (XSRF protection on)
- SC2 the same with a foreign Origin: no `Access-Control-Allow-Origin` (CORS protection on)
- SC3 a WebSocket upgrade of /_stcore/stream with this URL's own Origin: 101 (the host is in server.allowedHosts)
- SC4 the same with a foreign Origin: not 101
- SC5 (--foreign-host-check, local only: Cloud Run routes a foreign Host elsewhere) a foreign Host: not 101

The upgrade is closed without sending a frame, so no Streamlit session runs the app script and nothing queries BigQuery. A foreign
Origin makes Streamlit itself look up its external IP before refusing (F18). Standard library only (Python >= 3.12: it runs under
the runners' system python3). Exit 3 if any check fails, else 5 if any could not run, else 0.

Usage: python scripts/dashboard_server_check.py --url URL [--url URL ...] [--connect HOST:PORT] [--foreign-host-check]
"""

from __future__ import annotations

import argparse
import base64
import http.client
import os
import socket
import ssl
import sys
from urllib.parse import urlsplit

FOREIGN_ORIGIN = "https://evil.example"
FOREIGN_HOST = "evil.example"
TIMEOUT_S = 30


def header(headers: list[tuple[str, str]], name: str) -> list[str]:
    return [value for key, value in headers if key.lower() == name.lower()]


def upgrade_headers(host: str, origin: str, key: str) -> dict[str, str]:
    return {
        "Host": host,
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": key,
        "Sec-WebSocket-Protocol": "streamlit",
        "Origin": origin,
    }


def check_health(status: int, headers: list[tuple[str, str]], body: bytes) -> tuple[bool, str]:
    cache = header(headers, "Cache-Control")
    xsrf = any(cookie.startswith("_streamlit_xsrf=") for cookie in header(headers, "Set-Cookie"))
    ok = status == 200 and body.strip() == b"ok" and cache == ["no-cache"] and xsrf
    return ok, f"status {status}, body {body[:20]!r}, cache-control {cache}, xsrf cookie {xsrf}"


def check_cors(headers: list[tuple[str, str]]) -> tuple[bool, str]:
    acao = header(headers, "Access-Control-Allow-Origin")
    return not acao, f"access-control-allow-origin {acao}"


def check_upgrade(status: int, *, expect_101: bool) -> tuple[bool, str]:
    return (status == 101) == expect_101, f"status {status} (expected {'101' if expect_101 else 'not 101'})"


def _connection(url: str, connect: str | None) -> http.client.HTTPConnection:
    parts = urlsplit(url)
    if connect:
        host, _, port = connect.rpartition(":")
        return http.client.HTTPConnection(host, int(port), timeout=TIMEOUT_S)
    if parts.scheme == "https":
        return http.client.HTTPSConnection(parts.netloc, timeout=TIMEOUT_S, context=ssl.create_default_context())
    return http.client.HTTPConnection(parts.netloc, timeout=TIMEOUT_S)


def _request(url: str, connect: str | None, path: str, headers: dict[str, str]) -> tuple[int, list[tuple[str, str]], bytes]:
    conn = _connection(url, connect)
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        # A 101 response has no body to read; the socket is closed without a WebSocket frame.
        body = b"" if response.status == 101 else response.read()
        return response.status, response.getheaders(), body
    finally:
        conn.close()


def run(urls: list[str], *, connect: str | None, foreign_host_check: bool) -> list[tuple[str, str, str, str]]:
    """(check, url, outcome pass|fail|error, detail) for every check and URL."""
    results = []

    def attempt(name: str, url: str, path: str, headers: dict[str, str], judge) -> None:
        try:
            status, response_headers, body = _request(url, connect, path, headers)
        except (OSError, http.client.HTTPException) as exc:
            results.append((name, url, "error", f"{type(exc).__name__}: {exc}"))
            return
        ok, detail = judge(status, response_headers, body)
        results.append((name, url, "pass" if ok else "fail", detail))

    for url in urls:
        parts = urlsplit(url)
        host, own_origin = parts.netloc, f"{parts.scheme}://{parts.netloc}"

        def key() -> str:
            return base64.b64encode(os.urandom(16)).decode()

        attempt("SC1", url, "/_stcore/health", {"Host": host}, check_health)
        attempt("SC2", url, "/_stcore/health", {"Host": host, "Origin": FOREIGN_ORIGIN}, lambda s, h, b: check_cors(h))
        attempt("SC3", url, "/_stcore/stream", upgrade_headers(host, own_origin, key()),
                lambda s, h, b: check_upgrade(s, expect_101=True))
        attempt("SC4", url, "/_stcore/stream", upgrade_headers(host, FOREIGN_ORIGIN, key()),
                lambda s, h, b: check_upgrade(s, expect_101=False))
        if foreign_host_check:
            attempt("SC5", url, "/_stcore/stream", upgrade_headers(FOREIGN_HOST, f"http://{FOREIGN_HOST}", key()),
                    lambda s, h, b: check_upgrade(s, expect_101=False))
    return results


def exit_code(results: list[tuple[str, str, str, str]]) -> int:
    outcomes = {outcome for _, _, outcome, _ in results}
    return 3 if "fail" in outcomes else 5 if "error" in outcomes else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Server-level checks of the dashboard (SC1-SC5).")
    parser.add_argument("--url", action="append", required=True, help="base URL; its netloc is the Host header (repeatable)")
    parser.add_argument("--connect", help="HOST:PORT to send every request to, keeping each URL's Host header (plain HTTP)")
    parser.add_argument("--foreign-host-check", action="store_true", help="also run SC5 (local servers only)")
    args = parser.parse_args(argv)
    socket.setdefaulttimeout(TIMEOUT_S)
    results = run(args.url, connect=args.connect, foreign_host_check=args.foreign_host_check)
    for name, url, outcome, detail in results:
        print(f"{name} {url} {outcome} {detail}")
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
