"""scripts/publish_ledger.py (job B, v2 slice 7): stdlib only, contents API via `gh api --include`, never creates the
branch, skips unchanged files, large files via --input."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

from tests.scripts.stdlib_loader import Done, FakeGh, imported_modules, load_isolated


def _http(status: int, body: object = None) -> Done:
    return Done(returncode=0 if status < 400 else 1, stdout=f"HTTP/2.0 {status} X\nContent-Type: json\n\n" + (json.dumps(body) if body is not None else ""))


@pytest.fixture
def module(tmp_path):
    return load_isolated("publish_ledger", tmp_path)


@pytest.fixture
def ledger_dir(tmp_path):
    out = tmp_path / "ledger_out"
    (out / "docs").mkdir(parents=True)
    (out / "docs" / "run-ledger.md").write_text("# Run ledger\n")
    (out / "docs" / "last-tick.json").write_text('{"schemaVersion": 1}\n')
    (out / "meta.json").write_text('{"sim_date": "2023-08-14", "run_id": "42"}\n')
    return out


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))


def test_stdlib_only():
    assert imported_modules("publish_ledger") <= set(sys.stdlib_module_names) | {"__future__"}


def test_new_and_changed_files_are_put_with_input_files(module, ledger_dir, tmp_path):
    puts = []

    def answer(argv):
        if argv[3].endswith("/branches/ledger"):
            return _http(200, {})
        if "PUT" in argv:
            puts.append(json.loads(Path(argv[argv.index("--input") + 1]).read_text()))
            return _http(201, {})
        if "run-ledger.md" in argv[3]:
            return _http(404, {})
        return _http(200, {"sha": "abc", "encoding": "base64", "content": "b2xk\n"})  # "old", with a newline

    gh = FakeGh(answer)
    assert module.main(["--in-dir", str(ledger_dir)], run=gh) == 0
    assert [p.get("sha") for p in puts] == [None, "abc"]
    assert puts[0]["message"] == "Update run ledger (sim 2023-08-14, run 42)" and puts[0]["branch"] == "ledger"
    assert base64.b64decode(puts[0]["content"]) == b"# Run ledger\n"
    assert all("--include" in c for c in gh.calls)
    assert (tmp_path / "summary.md").read_text() == "Ledger: 2 of 2 file(s) committed to `ledger`\n"


def test_unchanged_files_are_skipped(module, ledger_dir):
    files = {p: (ledger_dir / p).read_bytes() for p in ("docs/run-ledger.md", "docs/last-tick.json")}

    def answer(argv):
        if argv[3].endswith("/branches/ledger"):
            return _http(200, {})
        assert "PUT" not in argv
        path = argv[3].split("/contents/")[1].split("?")[0]
        encoded = base64.b64encode(files[path]).decode()
        return _http(200, {"sha": "s", "encoding": "base64", "content": encoded[:4] + "\n" + encoded[4:]})

    assert module.main(["--in-dir", str(ledger_dir)], run=FakeGh(answer)) == 0


def test_large_file_encoding_none_is_treated_as_changed(module, ledger_dir):
    (ledger_dir / "docs" / "run-ledger.md").write_bytes(b"x" * 900 * 1024)
    puts = []

    def answer(argv):
        if argv[3].endswith("/branches/ledger"):
            return _http(200, {})
        if "PUT" in argv:
            puts.append(json.loads(Path(argv[argv.index("--input") + 1]).read_text()))
            assert all(len(a) < 128 * 1024 for a in argv)  # the content never goes on the command line
            return _http(200, {})
        return _http(200, {"sha": "big", "encoding": "none", "content": ""})

    assert module.main(["--in-dir", str(ledger_dir)], run=FakeGh(answer)) == 0
    assert puts[0]["sha"] == "big" and len(base64.b64decode(puts[0]["content"])) == 900 * 1024


def test_missing_branch_or_http_error_fails(module, ledger_dir, tmp_path):
    assert module.main(["--in-dir", str(ledger_dir)], run=FakeGh(lambda argv: _http(404, {}))) == 1
    assert "does not exist" in (tmp_path / "summary.md").read_text()

    def answer(argv):
        return _http(200, {}) if argv[3].endswith("/branches/ledger") else _http(500, {})

    assert module.main(["--in-dir", str(ledger_dir)], run=FakeGh(answer)) == 1
