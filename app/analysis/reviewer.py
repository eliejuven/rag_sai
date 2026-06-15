"""
Phase 6 — Review / Critic Agent + Error Log.

Pure post-processing over a CompanyDossier + the Phase 4 SectionOutputs —
no LLM calls, runs in milliseconds. Three checks feed the v0 confidence score:

- 6.1 citation coverage: every "fact" TaggedStatement must carry >=1 Citation
  that resolves to a real source already present in the Dossier (same dedup
  key as sections.py's _EvidenceIndex / composer.py's _CitationIndex:
  (document_id, section, page_number)).
- 6.2 internal consistency: "R$ ..." figures mentioned in statement text are
  cross-checked against dossier.financial_line_items and
  dossier.disclosed_metrics; dossier.conflicts are checked for the
  "mention both values" convention.
- 6.3 confidence score: simple weighted heuristic combining FRE coverage,
  citation traceability, numeric consistency, and (Phase 2 deferred, so
  always 1.0 for now) disclosed-vs-computed divergence. Explicitly v0.

Findings are logged as ErrorLogEntry — never block generation (per TODO.md
Phase 6: "log rather than block" for this week).
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from app.analysis.schemas import (
    AnalysisRun,
    CompanyDossier,
    Citation,
    ErrorLogEntry,
    SectionOutput,
)
from app.scraper.fre_client import CREDIT_SECTIONS

_TOTAL_FRE_SECTIONS = len(CREDIT_SECTIONS)

_MAX_MESSAGE_TEXT_LEN = 120

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _citation_key(c: Citation) -> tuple:
    return (c.document_id, c.section, c.page_number)


def _short(text: str, max_len: int = _MAX_MESSAGE_TEXT_LEN) -> str:
    text = text.strip()
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# 6.1 — Citation coverage check
# ---------------------------------------------------------------------------


def _dossier_citation_keys(dossier: CompanyDossier) -> set[tuple]:
    keys = set()
    for li in dossier.financial_line_items:
        keys.add(_citation_key(li.citation))
    for m in dossier.disclosed_metrics:
        keys.add(_citation_key(m.citation))
    for f in dossier.qualitative_facts:
        keys.add(_citation_key(f.citation))
    return keys


def check_citation_coverage(dossier: CompanyDossier, sections: list[SectionOutput]) -> list[ErrorLogEntry]:
    """Every "fact" statement must have >=1 citation, and every citation must
    resolve to a real source already present in the Dossier."""
    known_keys = _dossier_citation_keys(dossier)
    errors: list[ErrorLogEntry] = []

    for section in sections:
        for idx, statement in enumerate(section.statements):
            if statement.type != "fact":
                continue
            location = f"section={section.section_id}, statement_idx={idx}"

            if not statement.citations:
                errors.append(
                    ErrorLogEntry(
                        severity="critical",
                        stage="generation",
                        message=f'"fact" statement has no citations: "{_short(statement.text)}"',
                        location=location,
                    )
                )
                continue

            for c in statement.citations:
                if _citation_key(c) not in known_keys:
                    errors.append(
                        ErrorLogEntry(
                            severity="critical",
                            stage="generation",
                            message=(
                                f'citation does not resolve to a Dossier source: '
                                f'{c.filename} {c.section or "—"} p.{c.page_number or "—"} '
                                f'(statement: "{_short(statement.text)}")'
                            ),
                            location=location,
                        )
                    )

    return errors


# ---------------------------------------------------------------------------
# 6.2 — Internal consistency check
# ---------------------------------------------------------------------------

# Matches "R$ 80.121 milhões", "R$80,121 bilhões", "R$ 1.234.567,89", "R$500 mil", ...
_CURRENCY_RE = re.compile(
    r"R\$\s*([\d.,]+)\s*(milh[õo]es|milh[ãa]o|bilh[õo]es|bilh[ãa]o|trilh[õo]es|trilh[ãa]o|mil)?",
    re.IGNORECASE,
)

_SCALE_MULTIPLIERS = {
    "mil": 1e3,
    "milhao": 1e6,
    "milhões": 1e6,
    "milhoes": 1e6,
    "bilhao": 1e9,
    "bilhões": 1e9,
    "bilhoes": 1e9,
    "trilhao": 1e12,
    "trilhões": 1e12,
    "trilhoes": 1e12,
}

# Matches a scale word as a whole token (word boundaries), longest-first so
# "milhões"/"bilhões" aren't mistaken for "mil".
_SCALE_WORD_RE = re.compile(
    r"\b(milh[õo]es|milh[ãa]o|bilh[õo]es|bilh[ãa]o|trilh[õo]es|trilh[ãa]o|mil)\b",
    re.IGNORECASE,
)

# Below this absolute value (R$), a "match" against the Dossier is meaningless
# noise (e.g. small ratios/percentages the regex misfires on).
_MIN_CHECK_VALUE = 1_000.0

# Relative tolerance for "this is the same number" — matches the >1% relative
# difference threshold used for FactConflict detection in dossier_builder.
_RELATIVE_TOLERANCE = 0.01


def _normalize_scale(scale: str | None) -> str:
    if not scale:
        return ""
    s = scale.lower()
    s = s.replace("ã", "a").replace("õ", "o")
    return s


def _candidate_amounts(raw: str) -> list[float]:
    """Parse a Brazilian-formatted number into one or more candidate float
    values. LLM-generated text mixes conventions for "." and "," — observed
    in practice writing both "R$80.121 milhões" (dot as thousands separator,
    "." removed -> 80121) and "R$ 50,199 milhões" (comma as thousands
    separator -> 50199) for genuinely thousands-grouped figures, alongside
    ordinary decimals like "R$ 42,7 bilhões" (-> 42.7) or "R$ 213,72 bilhões"
    (-> 213.72).

    When there's exactly one separator and exactly 3 digits after it, both
    readings (decimal and thousands-grouped) are ambiguous, so both are
    returned as candidates — the caller treats a match against EITHER as
    confirmation. Otherwise the last separator is the decimal point and any
    earlier separators are thousands separators (unambiguous)."""
    raw = raw.strip().rstrip(".,")
    if not raw:
        return []

    seps = [c for c in raw if c in ".,"]
    if not seps:
        try:
            return [float(raw)]
        except ValueError:
            return []

    last_sep = seps[-1]
    head, last_group = raw.rsplit(last_sep, 1)

    if len(seps) == 1 and len(last_group) == 3:
        candidates = []
        try:
            candidates.append(float(head + last_group))  # thousands-grouped
        except ValueError:
            pass
        try:
            candidates.append(float(f"{head}.{last_group}"))  # decimal
        except ValueError:
            pass
        return candidates

    try:
        return [float(f"{re.sub(r'[.,]', '', head)}.{last_group}")]
    except ValueError:
        return []


def _unit_multiplier(unit: str | None) -> float:
    if not unit:
        return 1.0
    m = _SCALE_WORD_RE.search(unit)
    if not m:
        return 1.0
    return _SCALE_MULTIPLIERS.get(_normalize_scale(m.group(1)), 1.0)


def _dossier_known_values(dossier: CompanyDossier) -> list[tuple[float, str]]:
    """Every "R$" figure already present in the Dossier, normalized to
    absolute reais, paired with a human-readable description."""
    values: list[tuple[float, str]] = []
    for m in dossier.disclosed_metrics:
        if m.value is None:
            continue
        values.append((m.value * _unit_multiplier(m.unit), f'{m.label} ({m.period_label})'))
    for li in dossier.financial_line_items:
        values.append((li.value, f'{li.description} [{li.account_code}] ({li.period_label})'))
    return values


def _matches_any(value: float, known_values: list[tuple[float, str]], tolerance: float) -> bool:
    for kv, _label in known_values:
        denom = max(abs(kv), 1.0)
        if abs(value - kv) / denom <= tolerance:
            return True
    return False


def _extract_currency_amounts(text: str) -> list[tuple[str, str, list[float]]]:
    """Returns (raw_number, scale, [candidate absolute values in reais]) for
    every "R$ ..." mention in text that parses to at least one usable number."""
    results = []
    for raw, scale in _CURRENCY_RE.findall(text):
        candidates = _candidate_amounts(raw)
        if not candidates:
            continue
        mult = _SCALE_MULTIPLIERS.get(_normalize_scale(scale), 1.0)
        results.append((raw, scale, [c * mult for c in candidates]))
    return results


def check_internal_consistency(
    dossier: CompanyDossier,
    sections: list[SectionOutput],
    tolerance: float = _RELATIVE_TOLERANCE,
) -> list[ErrorLogEntry]:
    """Cross-check "R$ ..." mentions in fact/inference statements against
    dossier.financial_line_items / disclosed_metrics, and check
    dossier.conflicts are mentioned with both values where used."""
    known_values = _dossier_known_values(dossier)
    errors: list[ErrorLogEntry] = []

    for section in sections:
        for idx, statement in enumerate(section.statements):
            if statement.type not in ("fact", "inference"):
                continue
            for raw, scale, candidates in _extract_currency_amounts(statement.text):
                plausible = [c for c in candidates if c >= _MIN_CHECK_VALUE]
                if not plausible:
                    continue
                if not any(_matches_any(c, known_values, tolerance) for c in plausible):
                    mention = f"R$ {raw} {scale}".strip()
                    readings = " / ".join(f"R$ {c:,.0f}" for c in plausible)
                    errors.append(
                        ErrorLogEntry(
                            severity="warning",
                            stage="validation",
                            message=(
                                f'statement cites "{mention}" (~{readings}) — not found '
                                f'among Dossier financial_line_items/disclosed_metrics within '
                                f'{tolerance:.0%} tolerance: "{_short(statement.text)}"'
                            ),
                            location=f"section={section.section_id}, statement_idx={idx}",
                        )
                    )

    errors.extend(_check_conflicts_mentioned(dossier, sections))
    return errors


def _check_conflicts_mentioned(dossier: CompanyDossier, sections: list[SectionOutput]) -> list[ErrorLogEntry]:
    """For each Dossier.conflicts entry, if the output mentions one of the
    conflicting values, verify the other(s) are mentioned too ("mention
    both" convention)."""
    if not dossier.conflicts:
        return []

    all_amounts: set[float] = set()
    for section in sections:
        for statement in section.statements:
            for _raw, _scale, candidates in _extract_currency_amounts(statement.text):
                for absolute in candidates:
                    all_amounts.add(round(absolute, -3))  # bucket to nearest thousand

    errors: list[ErrorLogEntry] = []
    for conflict in dossier.conflicts:
        mentioned = sum(1 for value, _citation in conflict.values if round(value, -3) in all_amounts)
        if 0 < mentioned < len(conflict.values):
            errors.append(
                ErrorLogEntry(
                    severity="warning",
                    stage="validation",
                    message=(
                        f'unresolved Dossier conflict "{conflict.description}" — only '
                        f'{mentioned}/{len(conflict.values)} conflicting values are mentioned '
                        f'in the output (expected "mention both")'
                    ),
                    location=None,
                )
            )

    return errors


# ---------------------------------------------------------------------------
# 6.3 — Confidence score (v0)
# ---------------------------------------------------------------------------


def build_limitations(dossier: CompanyDossier) -> list[str]:
    """Human-readable limitations derived from DossierCoverage + conflicts."""
    limitations: list[str] = []
    cov = dossier.coverage

    if not cov.dfp_years:
        limitations.append("Nenhum dado anual (DFP) disponível.")
    if not cov.itr_years:
        limitations.append("Nenhum dado trimestral (ITR) disponível.")
    if cov.fre_sections_missing:
        labels = ", ".join(sorted(cov.fre_sections_missing))
        limitations.append(
            f"{len(cov.fre_sections_missing)}/{_TOTAL_FRE_SECTIONS} seções do FRE não "
            f"disponíveis: {labels}."
        )
    if dossier.conflicts:
        limitations.append(
            f"{len(dossier.conflicts)} conflito(s) entre fontes de dados ainda não resolvido(s)."
        )

    return limitations


def compute_confidence(
    dossier: CompanyDossier,
    sections: list[SectionOutput],
    error_log: list[ErrorLogEntry],
) -> tuple[float, dict[str, float]]:
    """Simple weighted heuristic (v0) — interpretable breakdown, not a black box."""
    coverage_score = (
        len(dossier.coverage.fre_sections_present) / _TOTAL_FRE_SECTIONS if _TOTAL_FRE_SECTIONS else 1.0
    )

    fact_statements = [s for sec in sections for s in sec.statements if s.type == "fact"]
    cited_facts = [s for s in fact_statements if s.citations]
    traceability_score = len(cited_facts) / len(fact_statements) if fact_statements else 1.0

    numeric_statements = [
        s
        for sec in sections
        for s in sec.statements
        if s.type in ("fact", "inference") and _CURRENCY_RE.search(s.text)
    ]
    numeric_warnings = sum(1 for e in error_log if e.stage == "validation")
    consistency_score = (
        max(0.0, 1.0 - numeric_warnings / len(numeric_statements)) if numeric_statements else 1.0
    )

    # Phase 2 (Calculation Engine) is deferred — no disclosed-vs-computed
    # divergence to compare yet, so this component is always 1.0 for now.
    divergence_score = 1.0

    breakdown = {
        "coverage": round(coverage_score, 3),
        "traceability": round(traceability_score, 3),
        "consistency": round(consistency_score, 3),
        "divergence": round(divergence_score, 3),
    }
    score = round(sum(breakdown.values()) / len(breakdown), 3)
    return score, breakdown


# ---------------------------------------------------------------------------
# Orchestration + persistence (6.4)
# ---------------------------------------------------------------------------


def review(
    dossier: CompanyDossier, sections: list[SectionOutput]
) -> tuple[list[ErrorLogEntry], list[str], float, dict[str, float]]:
    """Run all Phase 6 checks. Returns (error_log, limitations, confidence_score, breakdown)."""
    error_log = check_citation_coverage(dossier, sections) + check_internal_consistency(dossier, sections)
    limitations = build_limitations(dossier)
    score, breakdown = compute_confidence(dossier, sections, error_log)
    return error_log, limitations, score, breakdown


def build_analysis_run(
    dossier: CompanyDossier,
    sections: list[SectionOutput],
    one_pager_md: str,
    memo_md: str,
) -> AnalysisRun:
    error_log, limitations, score, breakdown = review(dossier, sections)
    return AnalysisRun(
        cnpj=dossier.cnpj,
        company_name=dossier.name,
        generated_at=datetime.now(timezone.utc),
        one_pager_md=one_pager_md,
        memo_md=memo_md,
        sections=sections,
        limitations=limitations,
        error_log=error_log,
        confidence_score=score,
        confidence_breakdown=breakdown,
    )


def persist_analysis_run(run: AnalysisRun, base_dir: str = "data/analysis_runs") -> Path:
    """Persist one_pager.md, memo.md, and run.json (incl. error_log) under
    data/analysis_runs/<cnpj_digits>/<timestamp>/."""
    cnpj_digits = re.sub(r"\D", "", run.cnpj)
    timestamp = run.generated_at.strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(base_dir) / cnpj_digits / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "one_pager.md").write_text(run.one_pager_md, encoding="utf-8")
    (out_dir / "memo.md").write_text(run.memo_md, encoding="utf-8")
    (out_dir / "run.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")

    return out_dir
