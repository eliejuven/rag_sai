"""
Test script for app/analysis/sections.py's output parsing.

Covers _strip_bracket_refs() and _parse_section_output(): the LLM
occasionally embeds leaked bracket-style references (Dossier evidence-index
ids, account codes, "[Manual Setorial §N]") inside "text"/"derived_from"/
"basis" despite the prompt rule against it (see TODO.md Phase 4
"Citation-discipline gap"). These must be stripped deterministically so the
composed output never shows raw, unresolved brackets next to its own [n]
citation markers.

No LLM calls — pure parsing logic, runs in milliseconds.

Usage: python3 test_sections_parsing.py
"""

import json

from app.analysis.sections import _AGENTS_BY_ID, _EvidenceIndex, _parse_section_output, _strip_bracket_refs


def test_strip_bracket_refs():
    assert _strip_bracket_refs("dívida líquida expandida (R$101.958 milhões) [25, 32]") == "dívida líquida expandida (R$101.958 milhões)"
    assert _strip_bracket_refs("...analisados [2.03.01, 2.03.09].") == "...analisados."
    assert _strip_bracket_refs("texto [Manual Setorial §1] continua") == "texto continua"
    assert _strip_bracket_refs("sem colchetes") == "sem colchetes"


def test_parse_section_output_strips_leaked_brackets():
    raw = json.dumps({"statements": [
        {"type": "fact", "text": "Receita caiu [25].", "citation_ids": []},
        {"type": "inference", "text": "Margem caiu.", "derived_from": ["EBITDA 2024 [25, 29]"], "citation_ids": []},
        {"type": "judgment", "text": "Risco moderado [2.01.06].", "basis": "Manual Setorial §1 [1]"},
    ]})
    out = _parse_section_output(raw, _AGENTS_BY_ID["risk_contingencies"], _EvidenceIndex())

    assert out.statements[0].text == "Receita caiu."
    assert out.statements[1].derived_from == ["EBITDA 2024"]
    assert out.statements[2].text == "Risco moderado."
    assert out.statements[2].basis == "Manual Setorial §1"


if __name__ == "__main__":
    test_strip_bracket_refs()
    test_parse_section_output_strips_leaked_brackets()
    print("OK")
