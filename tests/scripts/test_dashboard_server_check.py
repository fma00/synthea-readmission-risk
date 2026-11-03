"""scripts/dashboard_server_check.py (v3 slice 4, Verification V-SC): the pure judges, and SC1-SC5 against a REAL Streamlit
server (a one-line app with the repo's .streamlit/config.toml), plus a negative control proving each check can fail. The server
never runs the dashboard app, so nothing calls Google; SC4/SC5 make Streamlit itself look up its external IP (F18). Collected
under the Python 3.12 pipeline-dataproc-runtime job too (no streamlit there): the server tests skip, the pure ones run."""

from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests.scripts.stdlib_loader import ROOT, imported_modules, load_isolated
from tests.service_hosts import SERVICE_HOSTS

needs_streamlit = pytest.mark.skipif(importlib.util.find_spec("streamlit") is None, reason="needs streamlit (the dashboard set)")


@pytest.fixture
def check(tmp_path):
    return load_isolated("dashboard_server_check", tmp_path)


def test_stdlib_only():
    assert imported_modules("dashboard_server_check") <= set(sys.stdlib_module_names) | {"__future__"}


def test_judges(check):
    good = [("Cache-Control", "no-cache"), ("Set-Cookie", "a=1"), ("set-cookie", "_streamlit_xsrf=2|x|y|z; Path=/")]
    assert check.check_health(200, good, b"ok")[0]
    assert not check.check_health(200, good[:2], b"ok")[0]  # no XSRF cookie
    assert not check.check_health(503, good, b"ok")[0] and not check.check_health(200, good, b"loading")[0]
    assert check.check_cors([("Content-Type", "text/plain")])[0]
    assert not check.check_cors([("access-control-allow-origin", "*")])[0]
    assert check.check_upgrade(101, expect_101=True)[0] and not check.check_upgrade(403, expect_101=True)[0]
    assert check.check_upgrade(403, expect_101=False)[0] and not check.check_upgrade(101, expect_101=False)[0]
    headers = check.upgrade_headers("h.example", "https://h.example", "k")
    assert headers["Host"] == "h.example" and headers["Origin"] == "https://h.example" and headers["Upgrade"] == "websocket"


def test_exit_code(check):
    assert check.exit_code([("SC1", "u", "pass", "")]) == 0
    assert check.exit_code([("SC1", "u", "error", ""), ("SC2", "u", "pass", "")]) == 5
    assert check.exit_code([("SC1", "u", "error", ""), ("SC2", "u", "fail", "")]) == 3


def test_a_connection_error_is_reported_for_every_check(check, capsys):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again: nothing listens there
    assert check.main(["--connect", f"127.0.0.1:{port}", "--url", "http://x.example"]) == 5
    lines = capsys.readouterr().out.strip().split("\n")
    assert [line.split()[0] for line in lines] == ["SC1", "SC2", "SC3", "SC4"]
    assert all(line.split()[2] == "error" for line in lines)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(tmp_path: Path, config: str):
    (tmp_path / ".streamlit").mkdir()
    (tmp_path / ".streamlit" / "config.toml").write_text(config)
    (tmp_path / "app.py").write_text('import streamlit as st\nst.write("ok")\n')
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("STREAMLIT_")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", f"--server.port={port}", "--server.address=127.0.0.1"],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as r:
                if r.status == 200:
                    return proc, port
        except OSError:
            time.sleep(0.3)
    proc.kill()
    raise AssertionError(f"streamlit did not start: {proc.communicate()[0][-2000:]!r}")


def _urls(port: int) -> list[str]:
    return [arg for host in (*SERVICE_HOSTS, f"127.0.0.1:{port}") for arg in ("--url", f"http://{host}")]


@needs_streamlit
def test_the_repo_config_passes_every_check(check, tmp_path, capsys):
    proc, port = _serve(tmp_path, (ROOT / ".streamlit" / "config.toml").read_text())
    try:
        code = check.main(["--connect", f"127.0.0.1:{port}", "--foreign-host-check", *_urls(port)])
    finally:
        proc.kill()
        proc.wait()
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.count(" pass ") == 5 * (len(SERVICE_HOSTS) + 1)


@needs_streamlit
def test_a_permissive_config_fails_the_checks(check, tmp_path, capsys):
    config = (ROOT / ".streamlit" / "config.toml").read_text()
    permissive = (
        config.replace("enableXsrfProtection = true", "enableXsrfProtection = false")
        .replace("enableCORS = true", "enableCORS = false")
        .replace(config[config.index("allowedHosts = ["):].split("\n")[0], "allowedHosts = []")
    )
    assert permissive.count("= false") >= config.count("= false") + 2 and "allowedHosts = []" in permissive
    proc, port = _serve(tmp_path, permissive)
    try:
        code = check.main(["--connect", f"127.0.0.1:{port}", "--foreign-host-check", "--url", f"http://127.0.0.1:{port}"])
    finally:
        proc.kill()
        proc.wait()
    outcomes = {line.split()[0]: line.split()[2] for line in capsys.readouterr().out.strip().split("\n")}
    assert code == 3
    assert outcomes == {"SC1": "fail", "SC2": "fail", "SC3": "pass", "SC4": "fail", "SC5": "fail"}
