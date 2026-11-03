"""Loads a stdlib-only script by path from a directory holding ONLY that script (proves it is self-contained), and a
fake `gh` that records argv and answers from a script."""

from __future__ import annotations

import ast
import importlib.util
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_isolated(name: str, tmp_path: Path, *, directory: Path = ROOT / "scripts"):
    """Each module gets its own directory (tmp_path/isolated/<name>), so one test can load two (v3 slice 4)."""
    target = tmp_path / "isolated" / name / f"{name}.py"
    target.parent.mkdir(parents=True)
    shutil.copyfile(directory / f"{name}.py", target)
    spec = importlib.util.spec_from_file_location(f"isolated_{name}", target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def imported_modules(name: str, *, directory: Path = ROOT / "scripts") -> set[str]:
    """Top-level names (collections.abc -> collections): compare with sys.stdlib_module_names | {"__future__"}."""
    tree = ast.parse((directory / f"{name}.py").read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out.add((node.module or "").split(".")[0])
    return out


@dataclass
class Done:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeGh:
    answer: callable
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv, **kwargs):
        assert argv[0] == "gh" and kwargs.get("check") is False and "shell" not in kwargs
        self.calls.append(list(argv))
        return self.answer(argv)
