"""The dashboard's chrome (PRD v3 FR10): the synthetic-data notice, the honesty strip built from the scorer's DEMO_NOTICE (never
a copy), and the provenance footer. Every value it puts into markdown is validated or formatted here. See
notes/eg-new-feature/v3-slice3-dashboard-app-2026-11-01.md (Interfaces section 7, E5).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from readmission_risk.scoring.constants import DEMO_NOTICE
from readmission_risk.warehouse.bigquery import SERVING_GOLD_FINGERPRINT

from .present import short_id

SYNTHETIC_NOTICE = "**Synthetic data · demo only · not for clinical use.**"
INTENDED_USE = "**Intended use:** a simulated weekly triage call sheet for a fictional clinical team; a portfolio demo."
# E5: the model card arrives in slice 5 ("https://github.com/fma00/synthea-readmission-risk/blob/main/docs/model-card.md");
# until docs/model-card.md exists the strip names it without a link (a test keeps the two consistent).
MODEL_CARD_URL: str | None = None
SYNTHEA_URL = "https://github.com/synthetichealth/synthea"
SYNTHEA_LICENSE_URL = SYNTHEA_URL + "/blob/master/LICENSE"
GIT_COMMIT_ENV = "READMISSION_RISK_GIT_COMMIT"  # set by slice 4's image build
_COMMIT_RE = re.compile(r"[0-9a-f]{7,40}")


def honesty_strip() -> str:
    card = (
        f"**Model card:** [docs/model-card.md]({MODEL_CARD_URL})"
        if MODEL_CARD_URL
        else "**Model card:** in preparation (published with v3.0)."
    )
    return "  \n".join([INTENDED_USE, DEMO_NOTICE, card])


def git_commit(environ: Mapping[str, str]) -> str | None:
    value = environ.get(GIT_COMMIT_ENV)
    return value if isinstance(value, str) and _COMMIT_RE.fullmatch(value) else None


def footer(*, commit: str | None, bundles: Iterable) -> str:
    shown = []
    for label in (short_id(b) for b in sorted({b for b in bundles if b is not None}, key=str)):
        if label not in shown:
            shown.append(label)
    parts = [
        "Model bundle " + ", ".join(f"`{s}`" for s in shown) if shown else "Model bundle: none shown",
        f"Gold `{SERVING_GOLD_FINGERPRINT[:12]}…`",
        f"Image commit `{commit[:12]}`" if commit else "Image commit unknown (not a deployed image)",
        f"Synthetic patients generated with [Synthea]({SYNTHEA_URL}) ([Apache License 2.0]({SYNTHEA_LICENSE_URL}))",
    ]
    return " · ".join(parts)
