"""Lockfile drift checks: each pip-compile lockfile must be pip-compile's output for its current `.in` file.

This file sits at the ``tests/`` root, like test_tooling.py, because it tests repository files, not a package under
``src/``. It needs no JVM, no data and no network, and imports only the standard library, pytest and ``packaging``
(hash-pinned in requirements-dev.txt as a pytest dependency). It runs in CI's lint-and-test job, not in
pipeline-dataproc-runtime (that job runs only tests/pipeline). Design: notes/eg-new-feature/ci-hygiene-lockfiles-2026-10-12.md.

What it proves, structurally: the interpreter and pip-compile flags recorded in each header, that every `.in`
requirement is pinned and satisfied, that the `# via -r <in>` set equals the `.in` names (catches additions AND
removals), that every pin has a known parent and a hash. Blind spot: it cannot tell whether a fresh resolve would pick
a different transitive version, nor detect a hand edit of a transitive version or hash that keeps the graph consistent
(`--require-hashes` installs catch hashes that do not match PyPI's files). Nor does it protect against scoring guard 8:
a properly recompiled bump of a guarded library (scikit-learn, xgboost, mlflow, pandas, numpy) passes here and is then
refused against the champion bundle; that check is a named follow-up.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
# major.minor of .python-version (e.g. "3.14.7" -> "3.14"): the main and dev lockfiles follow the dev interpreter.
DEV_PYTHON = ".".join((ROOT / ".python-version").read_text().strip().split(".")[:2])

# A whole pin line: name, optional extras, `==`, a version that cannot start with `=` (so `foo===1.0` is not a pin), then
# only an optional `; marker`, an optional trailing backslash and whitespace.
PIN_RE = re.compile(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;\\=][^\s;\\]*)\s*(?:;[^\\]*)?(?:\\)?\s*$")
PARENT_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+")


@dataclass(frozen=True)
class LockPair:
    in_file: str  # e.g. "requirements.in"
    lock_file: str  # e.g. "requirements.txt"
    python: str  # the interpreter pip-compile must have run under (header line 2)
    command: str  # the pip-compile invocation pip-compile records (header line 5, after "#    ")
    recipe: str  # the make target that recompiles it, named in failure messages


LOCK_PAIRS = (
    LockPair("requirements.in", "requirements.txt", DEV_PYTHON,
             "pip-compile --generate-hashes --no-index --output-file=requirements.txt requirements.in", "make lock-main"),
    LockPair("requirements-dev.in", "requirements-dev.txt", DEV_PYTHON,
             "pip-compile --allow-unsafe --generate-hashes --no-index --output-file=requirements-dev.txt requirements-dev.in",
             "make lock-dev"),
    LockPair("requirements-dashboard.in", "requirements-dashboard.txt", DEV_PYTHON,
             "pip-compile --generate-hashes --no-index --output-file=requirements-dashboard.txt requirements-dashboard.in",
             "make lock-dashboard"),
    LockPair("requirements-dataproc-3.0.in", "requirements-dataproc-3.0.txt", "3.12",
             "pip-compile --generate-hashes --no-index --output-file=requirements-dataproc-3.0.txt requirements-dataproc-3.0.in",
             "make lock-runtime"),
)
LINUX_EXTRAS = "requirements-linux-extras.txt"
# Hand-maintained Linux-only pins and the lockfile each complements (v3 slice 3, S17: the dashboard set has its own, so the
# production workflows' requirements-linux-extras.txt stays unchanged).
LINUX_EXTRAS_PAIRS = ((LINUX_EXTRAS, "requirements.txt"), ("requirements-dashboard-linux-extras.txt", "requirements-dashboard.txt"))
# The only `-c` constraint an .in file may name: the dashboard set is constrained by the main lockfile.
ALLOWED_CONSTRAINTS = ("requirements.txt",)
LINUX_MARKER = '; platform_system == "Linux"'


@dataclass(frozen=True)
class Pin:
    version: str
    parents: frozenset[str]  # raw `# via` entries: "-r requirements.in", "google-api-core", "google-api-core[grpc]"
    n_hashes: int


def parse_lockfile(text: str) -> dict[str, Pin]:
    """Pins keyed by canonical name (extras stripped). ValueError on a duplicate pin or any line it does not understand."""
    entries: dict[str, dict] = {}
    current: dict | None = None
    in_via_list = False
    for line in text.split("\n"):
        stripped = line.strip()
        match = PIN_RE.match(line)
        if match:
            name = canonicalize_name(match.group(1))
            if name in entries:
                raise ValueError(f"duplicate pin: {name}")
            current = entries[name] = {"version": match.group(2), "parents": set(), "n_hashes": 0}
            in_via_list = False
        elif not stripped:
            continue
        elif stripped.startswith("--hash=sha256:"):
            if current is None:
                raise ValueError(f"hash line outside a pin: {line}")
            current["n_hashes"] += 1
        elif stripped.startswith("-"):
            raise ValueError(f"unsupported lockfile option line: {line}")
        elif stripped.startswith("#"):
            if current is None:
                continue  # the header
            if stripped.startswith("# via "):
                current["parents"].add(stripped[len("# via "):].strip())
                in_via_list = False
            elif stripped == "# via":
                in_via_list = True
            elif in_via_list and stripped.startswith("#   "):
                current["parents"].add(stripped[len("#   "):].strip())
            else:
                in_via_list = False  # e.g. pip-compile's unsafe-packages section header
        else:
            raise ValueError(f"unrecognised lockfile line: {line}")
    return {
        name: Pin(entry["version"], frozenset(entry["parents"]), entry["n_hashes"]) for name, entry in entries.items()
    }


def parse_in_file(text: str) -> tuple[list[Requirement], list[str]]:
    """(requirements, constraint files). The only directive accepted is `-c <file>` for a file in ALLOWED_CONSTRAINTS."""
    requirements, constraints = [], []
    for raw in text.split("\n"):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            parts = line.split()
            if len(parts) == 2 and parts[0] == "-c" and parts[1] in ALLOWED_CONSTRAINTS:
                constraints.append(parts[1])
                continue
            raise ValueError(f"unsupported .in directive: {line}")
        requirements.append(Requirement(line))
    return requirements, constraints


def drift_problems(
    in_text: str, lock_text: str, *, pair: LockPair, constraints: Mapping[str, str] | None = None
) -> list[str]:
    """Every way `lock_text` is not pip-compile's output for `in_text`, as messages; empty when clean. `constraints` maps each
    `-c` file the .in names to that lockfile's text (D8: a shared pin must have the constraint's version)."""
    problems = []
    lines = lock_text.split("\n")

    def line(index: int) -> str:
        return lines[index] if index < len(lines) else ""

    expected = f"# This file is autogenerated by pip-compile with Python {pair.python}"
    if line(1) != expected:
        problems.append(f"D1 header line 2 is {line(1)!r}, expected {expected!r}")
    expected = f"#    {pair.command}"
    if line(4) != expected:
        problems.append(f"D2 header line 5 is {line(4)!r}, expected {expected!r}")

    pins = parse_lockfile(lock_text)
    requirements, constraint_files = parse_in_file(in_text)
    in_name = pair.in_file
    for requirement in requirements:
        name = canonicalize_name(requirement.name)
        if requirement.marker is not None:
            problems.append(f"D3 {name}: environment markers are not supported in {in_name}")
        elif name not in pins:
            problems.append(f"D3 {name}: in {in_name} but not pinned")
        elif not requirement.specifier.contains(pins[name].version, prereleases=True):
            problems.append(f"D3 {name}: pinned {pins[name].version} does not satisfy {requirement.specifier}")

    declared = {canonicalize_name(r.name) for r in requirements}
    direct = {name for name, pin in pins.items() if f"-r {in_name}" in pin.parents}
    for name in sorted(declared ^ direct):
        if name in declared:
            problems.append(f"D4 {name}: in {in_name} but not marked '# via -r {in_name}'")
        else:
            problems.append(f"D4 {name}: marked '# via -r {in_name}' but not in {in_name}")

    for name, pin in pins.items():
        if not pin.parents:
            problems.append(f"D5 {name}: no '# via' annotation")
        for parent in sorted(pin.parents):
            if parent.startswith(("-r ", "-c ")):
                continue
            parent_name = PARENT_NAME_RE.match(parent)
            if parent_name is None or canonicalize_name(parent_name.group(0)) not in pins:
                problems.append(f"D6 {name}: '# via' parent {parent!r} is not pinned")
        if pin.n_hashes < 1:
            problems.append(f"D7 {name}: no --hash line")

    for constraint_file in constraint_files:
        if constraints is None or constraint_file not in constraints:
            problems.append(f"D8 {constraint_file}: constraint file not provided")
            continue
        constrained = parse_lockfile(constraints[constraint_file])
        for name in sorted(pins.keys() & constrained.keys()):
            if pins[name].version != constrained[name].version:
                problems.append(f"D8 {name}: pinned {pins[name].version} but {constraint_file} pins {constrained[name].version}")
    return problems


# ---------------------------------------------------------------- the repository's lockfiles


@pytest.mark.parametrize("pair", LOCK_PAIRS, ids=[p.lock_file for p in LOCK_PAIRS])
def test_lock_pair_has_no_drift(pair):
    in_text = (ROOT / pair.in_file).read_text()
    constraints = {name: (ROOT / name).read_text() for name in parse_in_file(in_text)[1]}
    problems = drift_problems(in_text, (ROOT / pair.lock_file).read_text(), pair=pair, constraints=constraints)
    assert problems == [], (
        "\n".join(problems) + f"\nfix: edit {pair.in_file} if needed, then run {pair.recipe} (on macOS, see docs/setup.md)"
    )


@pytest.mark.parametrize(("extras_file", "lock_file"), LINUX_EXTRAS_PAIRS, ids=[e for e, _ in LINUX_EXTRAS_PAIRS])
def test_linux_extras_are_marked_hashed_and_not_in_their_lockfile(extras_file, lock_file):
    text = (ROOT / extras_file).read_text()
    extras = parse_lockfile(text)  # no D-rules: the file is hand-written and has no `# via`
    assert extras, f"{extras_file} pins nothing"
    unmarked = [line for line in text.split("\n") if PIN_RE.match(line) and LINUX_MARKER not in line]
    assert unmarked == [], f"pins without {LINUX_MARKER!r}: {unmarked}"
    assert [name for name, pin in extras.items() if pin.n_hashes < 1] == []
    names = sorted(extras.keys() & parse_lockfile((ROOT / lock_file).read_text()).keys())
    assert names == [], (
        f"{names} pinned in both {extras_file} and {lock_file}; was {lock_file} compiled on Linux? "
        "(the lock targets must run on macOS)"
    )


# ---------------------------------------------------------------- the rules, on synthetic text

PAIR = LockPair("x.in", "x.txt", "3.14", "pip-compile --generate-hashes --no-index --output-file=x.txt x.in", "make x")
CLEAN_IN = "alpha==1.0\n"
CLEAN_LOCK = """#
# This file is autogenerated by pip-compile with Python 3.14
# by the following command:
#
#    pip-compile --generate-hashes --no-index --output-file=x.txt x.in
#
alpha==1.0 \\
    --hash=sha256:aa
    # via -r x.in
beta==2.0 \\
    --hash=sha256:bb
    # via alpha
"""


def problems(in_text: str = CLEAN_IN, lock_text: str = CLEAN_LOCK, constraints: Mapping[str, str] | None = None) -> list[str]:
    return drift_problems(in_text, lock_text, pair=PAIR, constraints=constraints)


def test_clean_fixture_has_no_problems():
    assert problems() == []


def test_d1_wrong_python_header():
    lock = CLEAN_LOCK.replace("with Python 3.14", "with Python 3.12")
    assert problems(lock_text=lock) == [
        "D1 header line 2 is '# This file is autogenerated by pip-compile with Python 3.12', "
        "expected '# This file is autogenerated by pip-compile with Python 3.14'"
    ]


def test_d1_d2_truncated_lockfile():
    assert problems(lock_text="#\n") == [
        "D1 header line 2 is '', expected '# This file is autogenerated by pip-compile with Python 3.14'",
        "D2 header line 5 is '', expected '#    pip-compile --generate-hashes --no-index --output-file=x.txt x.in'",
        "D3 alpha: in x.in but not pinned",
        "D4 alpha: in x.in but not marked '# via -r x.in'",
    ]


def test_d2_flags_changed():
    lock = CLEAN_LOCK.replace("pip-compile --generate-hashes --no-index", "pip-compile --no-index")
    assert problems(lock_text=lock) == [
        "D2 header line 5 is '#    pip-compile --no-index --output-file=x.txt x.in', "
        "expected '#    pip-compile --generate-hashes --no-index --output-file=x.txt x.in'"
    ]


def test_d3_in_pin_bumped_without_recompile():
    assert problems(in_text="alpha==1.1\n") == ["D3 alpha: pinned 1.0 does not satisfy ==1.1"]


def test_d3_and_d4_requirement_added_to_in():
    assert problems(in_text=CLEAN_IN + "Gamma\n") == [
        "D3 gamma: in x.in but not pinned",
        "D4 gamma: in x.in but not marked '# via -r x.in'",
    ]


def test_d4_requirement_removed_from_in():
    assert problems(in_text="") == ["D4 alpha: marked '# via -r x.in' but not in x.in"]


def test_d3_marker_rejected():
    assert problems(in_text='alpha==1.0 ; sys_platform == "linux"\n') == [
        "D3 alpha: environment markers are not supported in x.in"
    ]


def test_d5_pin_without_via():
    assert problems(lock_text=CLEAN_LOCK.replace("    # via alpha\n", "")) == ["D5 beta: no '# via' annotation"]


def test_d6_parent_missing():
    lock = CLEAN_LOCK.replace("# via alpha", "# via delta")
    assert problems(lock_text=lock) == ["D6 beta: '# via' parent 'delta' is not pinned"]


def test_d7_pin_without_hash():
    assert problems(lock_text=CLEAN_LOCK.replace("    --hash=sha256:bb\n", "")) == ["D7 beta: no --hash line"]


def test_parse_lockfile_rejects_duplicate_pins():
    with pytest.raises(ValueError, match="duplicate pin: alpha"):
        parse_lockfile(CLEAN_LOCK + "Alpha==1.0 \\\n    --hash=sha256:cc\n    # via -r x.in\n")


def test_parse_lockfile_rejects_option_lines():
    with pytest.raises(ValueError, match="unsupported lockfile option line: --index-url https://x"):
        parse_lockfile("--index-url https://x\n" + CLEAN_LOCK)


def test_parse_lockfile_rejects_a_hash_outside_a_pin():
    with pytest.raises(ValueError, match="hash line outside a pin"):
        parse_lockfile("    --hash=sha256:aa\n" + CLEAN_LOCK)


@pytest.mark.parametrize(
    "line", ["foo>=1.0", "foo===1.0", "  alpha==1.0", "name @ https://example.invalid/x.whl", "stray text", "alpha==1.0 junk"]
)
def test_parse_lockfile_rejects_unrecognised_lines(line):
    with pytest.raises(ValueError, match="unrecognised lockfile line"):
        parse_lockfile(CLEAN_LOCK + line + "\n")


def test_parse_in_file_rejects_directives():
    with pytest.raises(ValueError, match=re.escape("unsupported .in directive: -r other.in")):
        parse_in_file("-r other.in\n")
    with pytest.raises(ValueError, match=re.escape("unsupported .in directive: -c other.txt")):
        parse_in_file("-c other.txt\n")


def test_parse_in_file_returns_the_allowed_constraint():
    requirements, constraints = parse_in_file("-c requirements.txt\nalpha==1.0\n")
    assert [r.name for r in requirements] == ["alpha"] and constraints == ["requirements.txt"]


CONSTRAINED_IN = "-c requirements.txt\nalpha==1.0\n"
CONSTRAINED_LOCK = CLEAN_LOCK.replace("    # via alpha\n", "    # via\n    #   -c requirements.txt\n    #   alpha\n")
MAIN_LOCK = "beta==2.0 \\\n    --hash=sha256:bb\n    # via -r requirements.in\n"


def test_d6_skips_constraint_parents_and_d8_accepts_equal_pins():
    assert problems(in_text=CONSTRAINED_IN, lock_text=CONSTRAINED_LOCK, constraints={"requirements.txt": MAIN_LOCK}) == []


def test_d8_constraint_mismatch():
    main = MAIN_LOCK.replace("beta==2.0", "beta==2.1")
    assert problems(in_text=CONSTRAINED_IN, lock_text=CONSTRAINED_LOCK, constraints={"requirements.txt": main}) == [
        "D8 beta: pinned 2.0 but requirements.txt pins 2.1"
    ]


def test_d8_constraint_file_not_provided():
    assert problems(in_text=CONSTRAINED_IN, lock_text=CONSTRAINED_LOCK) == ["D8 requirements.txt: constraint file not provided"]


def test_parse_lockfile_reads_extras_and_multiline_via():
    lock = CLEAN_LOCK + (
        "google-api-core[grpc]==2.42.0 \\\n"
        "    --hash=sha256:cc\n"
        "    # via\n"
        "    #   -r x.in\n"
        "    #   alpha\n"
        "    #   beta\n"
        "grpcio==1.0 \\\n"
        "    --hash=sha256:dd\n"
        "    # via google-api-core[grpc]\n"
    )
    in_text = "alpha==1.0\ngoogle-api-core[grpc]==2.42.0\n"
    assert parse_lockfile(lock)["google-api-core"] == Pin("2.42.0", frozenset({"-r x.in", "alpha", "beta"}), 1)
    assert problems(in_text=in_text, lock_text=lock) == []


def test_wildcard_specifier_is_satisfied():
    assert problems(in_text="alpha==1.*\n") == []
    lock = CLEAN_LOCK.replace("alpha==1.0", "alpha==2.0")
    assert problems(in_text="alpha==1.*\n", lock_text=lock) == ["D3 alpha: pinned 2.0 does not satisfy ==1.*"]
