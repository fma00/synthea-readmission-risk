"""scripts/mark_linux_only_pins.py (v3 slice 4, F10): make lock-dashboard's post-processing step."""

from __future__ import annotations

import sys

import pytest

from tests.scripts.stdlib_loader import imported_modules, load_isolated

LOCK = 'six==1.17.0 \\\n    --hash=sha256:aa\nwatchdog==6.0.0 \\\n    --hash=sha256:bb\n    # via streamlit\n'


@pytest.fixture
def mark(tmp_path):
    return load_isolated("mark_linux_only_pins", tmp_path)


def test_stdlib_only():
    assert imported_modules("mark_linux_only_pins") <= set(sys.stdlib_module_names) | {"__future__"}


def test_marks_exactly_the_named_pin_and_is_idempotent(mark):
    once = mark.mark(LOCK, ["watchdog"])
    assert once == LOCK.replace("watchdog==6.0.0 \\", 'watchdog==6.0.0 ; platform_system == "Linux" \\')
    assert mark.mark(once, ["watchdog"]) == once


def test_a_missing_pin_is_an_error(mark, tmp_path, capsys):
    with pytest.raises(ValueError, match="exactly one pin line"):
        mark.mark(LOCK, ["nope"])
    path = tmp_path / "lock.txt"
    path.write_text(LOCK)
    assert mark.main([str(path), "nope"]) == 1 and "ERROR: nope" in capsys.readouterr().err
    assert path.read_text() == LOCK
    assert mark.main([str(path), "watchdog"]) == 0 and "platform_system" in path.read_text()
