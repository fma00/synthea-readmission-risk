"""The champion pointer, deploy/champion.json: which bundle the scorer uses by default, pinned by the sha256 of that
bundle's manifest.json. Promotion is a PR editing it; rollback is `git revert`. Standard library only. See
notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md (Interfaces > bundles/champion.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .manifest import HEX64_RE

_KEYS = {"bundle_uri", "sha256"}


@dataclass(frozen=True)
class Champion:
    bundle_uri: str
    sha256: str  # sha256 of the bundle's manifest.json bytes


def read_champion(path: Path) -> Champion:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"no champion pointer at {path}: pass --bundle, or export a bundle and promote it (docs/scoring.md)"
        ) from None
    except OSError as exc:
        raise ValueError(f"champion.json: cannot read {path}: {exc}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"champion.json: {path} is not UTF-8 JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"champion.json: {path} must hold a JSON object; got {type(document).__name__}")  # noqa: TRY004 -- malformed input is a ValueError by design (the CLI's user-error type)
    if set(document) != _KEYS:
        raise ValueError(f"champion.json: {path} must have exactly the keys {sorted(_KEYS)}; got {sorted(document)}")
    bundle_uri, sha256 = document["bundle_uri"], document["sha256"]
    if type(bundle_uri) is not str or not bundle_uri:
        raise ValueError(f"champion.json: bundle_uri must be a non-empty string; got {bundle_uri!r}")
    if type(sha256) is not str or not HEX64_RE.fullmatch(sha256):
        raise ValueError(f"champion.json: sha256 must be 64 lowercase hex characters; got {sha256!r}")
    return Champion(bundle_uri=bundle_uri, sha256=sha256)


def champion_json(bundle_uri: str, manifest_sha256: str) -> str:
    """The committed file's exact format."""
    return json.dumps({"bundle_uri": bundle_uri, "sha256": manifest_sha256}, indent=2) + "\n"
