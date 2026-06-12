"""
Sector Playbook loader ("MIT Faculty Lens").

load_playbook(sector) returns the markdown text of a sector-specific
playbook (data/playbooks/<slug>.md), or data/playbooks/_default.md if no
sector-specific playbook exists yet. The pipeline never breaks for an
uncovered sector — it just gets the generic framework.

CompanyDossier.sector comes straight from CVM's SETOR_ATIV column, which
includes ~70 distinct values — about half of them "Emp. Adm. Part. - <X>"
(holding companies for sector X). Those are routed to the same playbook as
<X> via _slugify(), since a holding company's credit profile is driven by
the sector it operates in.
"""

import re
import unicodedata
from pathlib import Path

PLAYBOOK_DIR = Path(__file__).parent.parent.parent / "data" / "playbooks"
DEFAULT_PLAYBOOK = "_default.md"

_HOLDING_PREFIX_RE = re.compile(r"^Emp\.\s*Adm\.\s*Part(?:icipações)?\.?\s*-?\s*", re.IGNORECASE)


def _slugify(sector: str) -> str:
    """Map a CVM SETOR_ATIV value to a playbook filename slug.

    e.g. "Petróleo e Gás" -> "petroleo-e-gas"
         "Emp. Adm. Part. - Extração Mineral" -> "extracao-mineral"
         "Emp. Adm. Participações" -> "" (no underlying sector)
    """
    sector = _HOLDING_PREFIX_RE.sub("", sector).strip()
    ascii_only = unicodedata.normalize("NFKD", sector).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()


def load_playbook(sector: str | None) -> str:
    """Return playbook markdown for `sector`, falling back to the default."""
    if sector:
        slug = _slugify(sector)
        if slug:
            path = PLAYBOOK_DIR / f"{slug}.md"
            if path.exists():
                return path.read_text(encoding="utf-8")
    return (PLAYBOOK_DIR / DEFAULT_PLAYBOOK).read_text(encoding="utf-8")
