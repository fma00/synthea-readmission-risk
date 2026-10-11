"""Documentation checks: the README is the public front page, so its links, structure and numbers are tested.

This file sits at the ``tests/`` root (not under a sub-package) on purpose: it tests documentation, not a package under
``src/``. It needs no JVM, no data and no network.

What is checked, and why:
- LICENSE: the MIT text is present with the right copyright line.
- Links: every relative link and ``#anchor`` in README.md and docs/*.md resolves, with EXACT path case (a link that only
  works on a case-insensitive filesystem breaks on GitHub and Linux CI).
- Repository layout block: every path it lists exists.
- Numbers: every number on the README's first screen and in its "Results so far" section also occurs in docs/results.md,
  the single source of truth, the results-table rows are copied verbatim, and the population sentence's counts must
  co-occur on one line there. (The prose bullets are guarded only by number presence, a known limit: rewording a
  bullet's figures without touching docs/results.md is not detected.)
- Structure and honesty: the README has the agreed sections in order, labels planned work as planned, and avoids a
  short list of overclaiming phrases.
- CI: the workflow requests read-only token permissions, runs pushes only on main, pins every action to a commit SHA
  with a version comment, does not persist checkout credentials and runs zizmor online with a failing exit code;
  Dependabot covers github-actions (weekly, cooldown, grouped) and the Terraform provider in infra/ (weekly, cooldown).
- .gitignore: git itself is asked (`git check-ignore`) whether local-state and credential-style paths are ignored BY THE
  REPO's .gitignore (not by a local exclude file), whether shared files stay visible, and that no tracked file matches
  an ignore rule. These rules are the only thing standing between a `git add .` and a public credentials leak.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
DOCS = sorted((ROOT / "docs").glob("*.md"))
CHECKED_FILES = [README, *DOCS]

EXPECTED_H2 = [
    "Status",
    "60-second tour",
    "Architecture",
    "Results so far",
    "Example output",
    "Limitations",
    "About the data",
    "Roadmap",
    "What this shows",
    "Quick start",
    "How this was built",
    "Repository layout",
    "License and acknowledgements",
]
MUST_BE_IGNORED = [
    "bundles/0123/manifest.json",
    ".claude/settings.local.json",
    ".claude/worktrees/x/file",
    ".streamlit/secrets.toml",
    "src/readmission_risk/dashboard/.streamlit/secrets.toml",
    "my-project-1a2b3c4d5e6f.json",
    "credentials.json",
    "client_secret_123.apps.googleusercontent.com.json",
    "gcp-key.json",
    "sa-service-account.json",
    "service_account.json",
    "serviceaccount.json",
    "key.json",
    "keyfile.json",
    "gha-creds-abc123.json",
    "my-app-firebase-adminsdk-xyz.json",
    "secrets.toml",
    "prod.env",
    "token.json",
    "deploy.p12",
    "deploy.pem",
    "id.key",
    "data/raw/x.csv",
    "mlflow/mlflow.db",
    "docs/img/preview/reliability_light.png",
    "docs/img/.tmp-abc123/reliability_light.svg",
    ".env",
    ".env.local",
    ".venv-dataproc/bin/python",
    ".venv-lock312/bin/python",
    "infra/terraform.tfvars",
    "infra/prod.auto.tfvars",
    "infra/terraform.tfvars.json",
    "infra/terraform.tfstate",
    "infra/terraform.tfstate.backup",
    "infra/.terraform/providers/x",
    "infra/bootstrap_override.tf",
    "infra/override.tf",
    "infra/override.tf.json",
]
MUST_NOT_BE_IGNORED = [
    "infra/.terraform.lock.hcl",
    "infra/terraform.tfvars.example",
    "infra/storage.tf",
    "infra/bootstrap.sh",
    "deploy/champion.json",
    "src/readmission_risk/bundles/manifest.py",
    "tests/bundles/test_store.py",
    ".claude/commands/eg-prd.md",
    ".env.example",
    "src/readmission_risk/models/split.py",
    "docs/setup.md",
    "docs/img/reliability_light.svg",
    "LICENSE",
    "README.md",
    "tests/test_docs.py",
    "Makefile",
    ".pre-commit-config.yaml",
    "requirements-dataproc-3.0.txt",
    "requirements-dataproc-3.0.in",
]
HAS_GIT_CHECKOUT = (ROOT / ".git").exists() and shutil.which("git") is not None
FORBIDDEN_README_PHRASES = ["production-ready", "production ready", "state-of-the-art", "leakage-safe", "streamlit.app"]

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_LINK_RE = re.compile(r'\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
_NUMBER_RE = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{2,})%?(?!-days?\b)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _closes(fence: str, line: str) -> bool:
    """A closing fence: same character as the opener, at least as long, and nothing else on the line."""
    m = _FENCE_RE.match(line)
    return bool(m) and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and line.strip() == m.group(1)


def strip_fences(text: str) -> str:
    """Remove fenced code blocks (backtick or tilde fences at any indentation, including inside list items)."""
    out: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        m = _FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                continue
            out.append(line)
        elif _closes(fence, line):
            fence = None
    return "\n".join(out)


def strip_inline_code(text: str) -> str:
    return re.sub(r"(`+)[^\n]*?\1", "", text)


def heading_plain_text(heading: str) -> str:
    """Reduce markdown link syntax, backticks and emphasis markers to plain text (heading code text is kept)."""
    heading = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    return heading.replace("`", "").replace("*", "")


def slugify(heading: str) -> str:
    """GitHub's heading anchor rule: lowercase; drop everything except letters, digits, spaces, hyphens, underscores;
    each space becomes one hyphen (no collapsing). Dashes such as U+2212 or an em dash are dropped."""
    text = re.sub(r"[^\w\- ]", "", heading_plain_text(heading).lower())
    return text.replace(" ", "-")


def heading_slugs_from_text(text: str) -> list[str]:
    counts: dict[str, int] = {}
    slugs: list[str] = []
    for line in strip_fences(text).split("\n"):
        m = _HEADING_RE.match(line)
        if not m:
            continue
        base = slugify(m.group(2))
        n = counts.get(base, 0)
        slugs.append(base if n == 0 else f"{base}-{n}")
        counts[base] = n + 1
    return slugs


def exact_case_exists(path: Path, root: Path) -> bool:
    """True iff `path` exists and every segment below `root` has exactly the on-disk case."""
    path = Path(os.path.normpath(path))
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    here = root
    for part in parts:
        if not here.is_dir() or part not in {p.name for p in here.iterdir()}:
            return False
        here = here / part
    return True


def check_links(path: Path, root: Path = ROOT) -> list[str]:
    """Return a list of problems with the relative links and anchors in one markdown file."""
    body = strip_inline_code(strip_fences(_read(path)))
    problems: list[str] = []
    for target in _LINK_RE.findall(body):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        file_part, _, anchor = target.partition("#")
        dest = path if not file_part else path.parent / file_part
        if file_part and not exact_case_exists(dest, root):
            problems.append(f"{path.name}: link target does not exist (exact case): {target}")
            continue
        if anchor and dest.suffix == ".md" and dest.is_file() and anchor not in heading_slugs_from_text(_read(dest)):
            problems.append(f"{path.name}: anchor not found in {dest.name}: {target}")
    return problems


def split_sections(text: str) -> dict[str, list[str]]:
    """H2 heading -> its lines (fenced lines included), splitting only on H2s outside code fences."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    fence: str | None = None
    for line in text.split("\n"):
        m = _FENCE_RE.match(line)
        if fence is None and m:
            fence = m.group(1)
        elif fence is not None and _closes(fence, line):
            fence = None
        elif fence is None and line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def first_screen(text: str) -> list[str]:
    """README lines before the first H2 (outside fences)."""
    lines: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        m = _FENCE_RE.match(line)
        if fence is None and m:
            fence = m.group(1)
        elif fence is not None and _closes(fence, line):
            fence = None
        elif fence is None and line.startswith("## "):
            break
        lines.append(line)
    return lines


def normalise_dashes(text: str) -> str:
    return text.replace("−", "-").replace("–", "-").replace("—", "-")


def number_tokens(text: str) -> list[str]:
    """Numbers in prose: link targets are removed first (URLs and file names carry dates), dashes are normalised."""
    text = re.sub(r"\]\([^)]*\)", "]", normalise_dashes(text))
    return [m.group(0) for m in _NUMBER_RE.finditer(text)]


# ---------------------------------------------------------------- (a) license


def test_license_is_mit_with_copyright_line():
    text = _read(ROOT / "LICENSE")
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Florence Aellen" in text
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_pyproject_declares_the_license_and_a_setuptools_that_understands_it():
    """An SPDX `license` expression (PEP 639) needs setuptools >= 77; the expression must match the LICENSE file."""
    config = tomllib.loads(_read(ROOT / "pyproject.toml"))
    assert config["project"]["license"] == "MIT"
    assert config["project"]["license-files"] == ["LICENSE"]
    assert _read(ROOT / "LICENSE").startswith("MIT License")
    requirement = next(r for r in config["build-system"]["requires"] if r.startswith("setuptools"))
    floor = re.search(r">=\s*(\d+)", requirement)
    assert floor is not None and int(floor.group(1)) >= 77, requirement


# ---------------------------------------------------------------- (b) links


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        ("Batch scoring (Slice 4: Top-N triage list)", "batch-scoring-slice-4-top-n-triage-list"),
        (
            "Results and caveats (Slice 3, on the slice-2b gold table)",
            "results-and-caveats-slice-3-on-the-slice-2b-gold-table",
        ),
        ("The `foo` command", "the-foo-command"),
        ("Slice 3 — foo", "slice-3--foo"),
        ("**Bold** and [a link](x.md)", "bold-and-a-link"),
    ],
)
def test_slugify_matches_github_rule(heading, slug):
    assert slugify(heading) == slug


def test_repeated_headings_get_numeric_suffixes():
    assert heading_slugs_from_text("# A\n## A\n### A\n") == ["a", "a-1", "a-2"]


def test_code_fences_hide_shell_comments_from_heading_collection():
    text = "# Real\n\n```sh\n# not a heading\n```\n\n  ```\n  # also not\n  ```\n"
    assert heading_slugs_from_text(text) == ["real"]


def test_link_checker_reports_broken_targets_anchors_and_wrong_case(tmp_path):
    (tmp_path / "b.md").write_text("# Hello\n", encoding="utf-8")
    (tmp_path / "Real.md").write_text("# Real\n", encoding="utf-8")
    (tmp_path / "a.md").write_text(
        "[ok](b.md#hello) [missing](nope.md) [bad anchor](b.md#nowhere) [self](#nowhere-here) "
        "[wrong case](real.md) [external](https://example.com/x#y) `[in code](gone.md)`\n"
        "```\n[in fence](gone2.md)\n```\n",
        encoding="utf-8",
    )
    problems = check_links(tmp_path / "a.md", root=tmp_path)
    joined = "\n".join(problems)
    assert "nope.md" in joined
    assert "b.md#nowhere" in joined
    assert "#nowhere-here" in joined
    assert "real.md" in joined
    assert "b.md#hello" not in joined
    assert "gone" not in joined and "example.com" not in joined
    assert len(problems) == 4


@pytest.mark.parametrize("path", CHECKED_FILES, ids=lambda p: p.name)
def test_relative_links_and_anchors_resolve(path):
    assert path.is_file()
    assert check_links(path) == []


def test_docs_directory_has_the_three_pages():
    assert [p.name for p in DOCS] == ["results.md", "scoring.md", "setup.md"]


FENCE_CASES = [
    "intro\n````md\n```\n## inner\n```\n````\n## Real\nbody\n",  # longer outer fence containing a shorter one
    "intro\n~~~\n## inner\n~~~\n## Real\nbody\n",  # tilde fence
    "intro\n```\n```sh\n## inner\n```\n## Real\nbody\n",  # a line with an info string does not close a fence
]


@pytest.mark.parametrize("text", FENCE_CASES)
def test_section_splitting_ignores_headings_inside_fences(text):
    lines = text.split("\n")
    assert list(split_sections(text)) == ["Real"]
    assert first_screen(text) == lines[: lines.index("## Real")]
    assert "## inner" in first_screen(text)


def test_repository_layout_block_lists_existing_paths():
    lines = split_sections(_read(README))["Repository layout"]
    fenced: list[str] = []
    fence: str | None = None
    for line in lines:
        if fence is None:
            m = _FENCE_RE.match(line)
            fence = m.group(1) if m else None
        elif _closes(fence, line):
            fence = None
        elif line.strip():
            fenced.append(line.split()[0])
    assert fenced, "the layout block must list at least one path"
    missing = [p for p in fenced if not exact_case_exists(ROOT / p, ROOT)]
    assert missing == []


# ---------------------------------------------------------------- (c) numbers


def _results_region_and_first_screen() -> str:
    text = _read(README)
    screen = [line for line in first_screen(text) if not line.startswith("[![")]
    results = split_sections(text)["Results so far"]
    return "\n".join(screen + results)


def test_readme_numbers_occur_in_docs_results():
    docs = normalise_dashes(_read(ROOT / "docs" / "results.md"))
    tokens = number_tokens(_results_region_and_first_screen())
    assert tokens, "expected numbers on the first screen and in the results section"
    missing = [t for t in tokens if not re.search(rf"(?<![\w.]){re.escape(t)}(?![\w])", docs)]
    assert missing == [], f"numbers in the README that are not in docs/results.md: {missing}"


def test_readme_results_table_rows_are_verbatim_from_docs_results():
    def squash(line: str) -> str:
        return re.sub(r"\s+", " ", line.strip())

    readme_lines = split_sections(_read(README))["Results so far"]
    header = next(i for i, line in enumerate(readme_lines) if line.startswith("|") and "ROC-AUC" in line)
    rows = []
    for line in readme_lines[header + 1 :]:
        if not line.startswith("|"):
            break
        if set(line.replace("|", "").strip()) <= {"-", " ", ":"}:
            continue
        rows.append(squash(line))
    assert len(rows) == 3, rows
    docs_lines = {squash(line) for line in _read(ROOT / "docs" / "results.md").split("\n")}
    assert [r for r in rows if r not in docs_lines] == []


def test_population_counts_occur_together_in_one_line_of_docs_results():
    """Each number occurring somewhere in docs/results.md is weak evidence; the population sentence's counts must
    co-occur on ONE line there, so a stale README sentence cannot pass on unrelated matches (41, 96, ... recur)."""
    section = split_sections(_read(README))["Results so far"]
    sentence = next(line for line in section if line.startswith("Measured on"))
    tokens = number_tokens(sentence)
    assert {"10,000", "6,391", "96", "1,539", "41"} <= set(tokens), tokens
    docs_lines = normalise_dashes(_read(ROOT / "docs" / "results.md")).split("\n")

    def has(line: str, token: str) -> bool:
        return re.search(rf"(?<![\w.]){re.escape(token)}(?![\w])", line) is not None

    assert any(all(has(line, t) for t in tokens) for line in docs_lines), f"no single line of docs/results.md has all of {tokens}"


def test_number_tokenizer_rules():
    assert number_tokens("10,000 patients; 6,391 rows; 0.869; 95%; 1.50%; 41") == ["10,000", "6,391", "0.869", "95%", "1.50%", "41"]
    assert number_tokens("0.87–0.90 and −0.047") == ["0.87", "0.90", "0.047"]
    assert number_tokens("a 30-day window, 3 of 7, x2") == []
    assert number_tokens("see [notes](notes/x-2026-09-20.md)") == []


# ---------------------------------------------------------------- (d) structure and honesty


def test_readme_has_the_agreed_sections_in_order():
    assert list(split_sections(_read(README))) == EXPECTED_H2


def test_readme_has_one_mermaid_diagram():
    assert len(re.findall(r"^```mermaid\s*$", _read(README), flags=re.MULTILINE)) == 1


def test_first_screen_shows_status_limits_and_next_steps():
    lines = _read(README).split("\n")
    assert any(line.startswith("> [!NOTE]") for line in lines[:25]), "the work-in-progress alert must be near the top"
    head = "\n".join(lines[:40]).lower()
    for word in ("scale-up", "cloud", "dashboard", "limit"):
        assert word in head, f"first 40 lines of the README must mention {word!r}"


def test_roadmap_names_the_planned_stages_and_marks_them_not_started():
    roadmap = "\n".join(split_sections(_read(README))["Roadmap"])
    for word in ("Scale-up", "Cloud", "Dashboard", "BigQuery", "Streamlit"):
        assert word in roadmap
    assert roadmap.count("not started") >= 3


@pytest.mark.parametrize("stage", ["**Cloud**", "**Scale-up**", "**Dashboard**"])
def test_status_table_marks_planned_stages_as_planned(stage):
    rows = [line for line in split_sections(_read(README))["Status"] if line.startswith("|") and stage in line]
    assert len(rows) == 1, rows
    assert "Planned" in rows[0]


def test_readme_avoids_overclaiming_phrases():
    text = _read(README).lower()
    assert [p for p in FORBIDDEN_README_PHRASES if p in text] == []


# ---------------------------------------------------------------- (e) CI


def test_ci_workflow_requests_read_only_token_permissions():
    lines = _read(ROOT / ".github" / "workflows" / "ci.yml").split("\n")
    assert "permissions:" in lines, "ci.yml must have a top-level `permissions:` block"
    at = lines.index("permissions:")
    assert lines[at + 1] == "  contents: read"
    assert at < next(i for i, line in enumerate(lines) if line.startswith("jobs:"))


# Supply-chain baseline (notes/eg-new-feature/ci-supply-chain-hardening-2026-10-10.md). Line-based, like the test above:
# zizmor audits the workflows semantically in CI; these checks pin the decisions so that a change to them is deliberate.
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS_DIR / "ci.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
# The config files zizmor 1.30.1 discovers; any of them could disable audits or ignore findings.
ZIZMOR_CONFIGS = [ROOT / ".github" / "zizmor.yml", ROOT / ".github" / "zizmor.yaml", ROOT / "zizmor.yml", ROOT / "zizmor.yaml"]
_USES_RE = re.compile(r"^(\s*)(?:- )?uses:\s*(.+?)\s*$")
_PINNED_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}  # v\d+\.\d+\.\d+")
EXPECTED_DEPENDABOT = [
    "version: 2",
    "updates:",
    '  - package-ecosystem: "github-actions"',
    '    directory: "/"',
    "    schedule:",
    '      interval: "weekly"',
    "    cooldown:",
    "      default-days: 7",
    "    groups:",
    "      github-actions-minor-and-patch:",
    '        patterns: ["*"]',
    '        update-types: ["minor", "patch"]',
    '  - package-ecosystem: "terraform"',
    '    directory: "/infra"',
    "    schedule:",
    '      interval: "weekly"',
    "    cooldown:",
    "      default-days: 7",
]


def _workflow_files() -> list[Path]:
    return sorted([*WORKFLOWS_DIR.glob("*.yml"), *WORKFLOWS_DIR.glob("*.yaml")])


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block_end(lines: list[str], start: int, column: int) -> int:
    """Index of the first non-blank line after `start` indented at most `column` (or len(lines))."""
    return next((i for i in range(start + 1, len(lines)) if lines[i].strip() and _indent(lines[i]) <= column), len(lines))


def _step_block(lines: list[str], at: int) -> list[str]:
    """The lines of the step containing the `uses:` line at index `at`, whichever key the step starts with."""
    key_column = _indent(lines[at]) + (2 if lines[at].lstrip().startswith("- ") else 0)
    start = next(i for i in range(at, -1, -1) if re.match(r"^\s*- ", lines[i]) and _indent(lines[i]) < key_column)
    return lines[start : _block_end(lines, start, _indent(lines[start]))]


def _without_comments(lines: list[str]) -> list[str]:
    return [line.rstrip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def test_every_workflow_action_is_pinned_to_a_commit_sha_with_a_version_comment():
    found, offenders = 0, []
    for path in _workflow_files():
        for lineno, line in enumerate(_read(path).split("\n"), start=1):
            m = _USES_RE.match(line)
            if not m or m.group(2).startswith("./"):
                continue
            found += 1
            if not _PINNED_RE.fullmatch(m.group(2)):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert found, "no `uses:` line found in .github/workflows/"
    assert offenders == [], "pin every action to a full commit SHA followed by `  # vX.Y.Z`:\n" + "\n".join(offenders)


def test_checkout_steps_do_not_persist_credentials():
    found = 0
    for path in _workflow_files():
        lines = _read(path).split("\n")
        for at, line in enumerate(lines):
            m = _USES_RE.match(line)
            if not m or not m.group(2).startswith("actions/checkout@"):
                continue
            found += 1
            step = [s.strip() for s in _step_block(lines, at)]
            assert "persist-credentials: false" in step, f"{path.relative_to(ROOT)}:{at + 1}: checkout persists credentials"
    assert found, "no checkout step found in .github/workflows/"


def test_dependabot_config_is_weekly_with_cooldown_for_actions_and_terraform():
    assert DEPENDABOT.is_file(), ".github/dependabot.yml is missing"
    assert _without_comments(_read(DEPENDABOT).split("\n")) == EXPECTED_DEPENDABOT


def test_ci_audits_workflows_with_zizmor_online_and_blocking():
    text = _read(CI_WORKFLOW)
    lines = text.split("\n")
    stripped = [line.strip() for line in lines]
    assert stripped.count('run: zizmor --gh-token "$GH_TOKEN" .') == 1
    assert "GH_TOKEN: ${{ github.token }}" in stripped
    assert "continue-on-error" not in text
    assert [line for line in lines if re.match(r"^\s*(?:- )?if:", line)] == [], "an `if:` could switch the audit off"
    start = lines.index("  audit-workflows:")
    job = [line.strip() for line in lines[start : _block_end(lines, start, 2)]]
    assert "run: pip install --require-hashes -r requirements-dev.txt" in job
    for path in _workflow_files():
        assert "zizmor: ignore" not in _read(path), f"{path.relative_to(ROOT)} suppresses a zizmor finding inline"
    assert [p.name for p in ZIZMOR_CONFIGS if p.exists()] == [], "a zizmor config file could disable audits"
    assert "zizmor" in [line.strip() for line in _read(ROOT / "requirements-dev.in").split("\n")]
    lock = _read(ROOT / "requirements-dev.txt").split("\n")
    at = next((i for i, line in enumerate(lock) if line.startswith("zizmor==")), None)
    assert at is not None, "zizmor is not pinned in requirements-dev.txt"
    assert lock[at + 1].strip().startswith("--hash=sha256:")


def test_ci_push_trigger_runs_only_on_main():
    lines = _read(CI_WORKFLOW).split("\n")
    start = lines.index("on:")
    assert _without_comments(lines[start : _block_end(lines, start, 0)]) == [
        "on:",
        "  push:",
        "    branches: [main]",
        "  pull_request:",
    ]


# ---------------------------------------------------------------- .gitignore


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


@pytest.mark.skipif(not HAS_GIT_CHECKOUT, reason="needs a git checkout")
@pytest.mark.parametrize("path", MUST_BE_IGNORED)
def test_gitignore_ignores_local_state_and_credential_paths(path):
    result = _git("check-ignore", "-v", "--no-index", path)
    assert result.returncode in (0, 1), f"git check-ignore failed: {result.stderr.strip()}"
    assert result.returncode == 0, f"{path} is not ignored"
    source = result.stdout.split(":", 1)[0]
    assert source == ".gitignore", f"{path} is ignored by {source!r}, not by the repository's .gitignore"


@pytest.mark.skipif(not HAS_GIT_CHECKOUT, reason="needs a git checkout")
@pytest.mark.parametrize("path", MUST_NOT_BE_IGNORED)
def test_gitignore_keeps_shared_files_visible(path):
    result = _git("check-ignore", "-q", "--no-index", path)
    assert result.returncode in (0, 1), f"git check-ignore failed: {result.stderr.strip()}"
    assert result.returncode == 1, f"{path} would be ignored by git"


@pytest.mark.skipif(not HAS_GIT_CHECKOUT, reason="needs a git checkout")
def test_no_tracked_file_matches_an_ignore_rule():
    # Only the repository's own .gitignore files count: --exclude-standard would also honour a contributor's global
    # excludes file and .git/info/exclude, which would make this test depend on the machine it runs on.
    result = _git("ls-files", "-ci", "--exclude-per-directory=.gitignore")
    assert result.returncode == 0, f"git ls-files failed: {result.stderr.strip()}"
    assert result.stdout.split() == []


# ---------------------------------------------------------------- figure and sample table


def test_readme_figure_files_exist_and_use_both_themes():
    text = _read(README)
    sources = re.findall(r'(?:srcset|src)="(docs/img/[^"]+)"', text)
    assert sorted(sources) == ["docs/img/reliability_dark.svg", "docs/img/reliability_light.svg"]
    assert all(exact_case_exists(ROOT / p, ROOT) for p in sources)
    assert 'media="(prefers-color-scheme: dark)"' in text
    assert 'alt="' in text


def test_readme_sample_table_is_verbatim_from_the_generated_file():
    generated = _read(ROOT / "docs" / "img" / "sample_topn.md").strip("\n")
    section = "\n".join(split_sections(_read(README))["Example output"])
    assert generated in section, "README 'Example output' must contain docs/img/sample_topn.md verbatim; regenerate or re-paste"


def test_sample_table_rank_one_agrees_with_the_scoring_docs():
    top_row = next(line for line in _read(ROOT / "docs" / "img" / "sample_topn.md").splitlines() if line.startswith("| 1 |"))
    assert top_row.split("|")[2].strip() in _read(ROOT / "docs" / "scoring.md")


def test_readme_refusal_example_matches_what_the_code_raises():
    """The README quotes a real refusal; regenerate the message from the code (same counts and window) and compare, so a
    reworded error cannot leave a stale copy in the README."""
    import datetime as dt

    import pandas as pd

    from readmission_risk.scoring.selection import select_held_out_window

    line = next(line for line in _read(README).splitlines() if line.startswith("ERROR: window ["))
    counts = re.search(r"contains (\d+) in-sample .* and (\d+) discharge", line)
    assert counts is not None, line
    n_train, n_dropped = int(counts.group(1)), int(counts.group(2))
    stops = pd.date_range("2022-06-10", periods=n_train + n_dropped, freq="h", tz="UTC")
    gold = pd.DataFrame({"encounter_id": [f"e{i}" for i in range(len(stops))], "index_stop": stops})
    partition_of = pd.Series(["train"] * n_train, index=[f"e{i}" for i in range(n_train)])
    with pytest.raises(ValueError) as info:
        select_held_out_window(gold, partition_of, run_date=dt.date(2022, 6, 30), window_days=30)
    assert line == f"ERROR: {info.value}"
