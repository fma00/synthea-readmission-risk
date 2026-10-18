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


def load_isolated(name: str, tmp_path: Path):
    target = tmp_path / "isolated" / f"{name}.py"
    target.parent.mkdir()
    shutil.copyfile(ROOT / "scripts" / f"{name}.py", target)
    spec = importlib.util.spec_from_file_location(f"isolated_{name}", target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def imported_modules(name: str) -> set[str]:
    tree = ast.parse((ROOT / "scripts" / f"{name}.py").read_text())
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
