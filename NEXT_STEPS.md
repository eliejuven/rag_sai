# Next Steps — Improving v1 of the Credit Analysis Pipeline

> v1 is done and merged to `main`. This file is the lightweight tracker for
> the "improve it step by step" phase. For the full phase-by-phase history,
> architecture decisions, and open questions from building v1, see `TODO.md`
> (kept as a historical record, not actively updated going forward).

## Current state (2026-06-15)

- The full pipeline (Dossier → 9 section-generator agents → Composer
  (1-pager/memo) → Reviewer → orchestration API) is complete and working
  end-to-end. `/1-pager <empresa>` in the chat UI streams progress and
  returns a 1-pager (Markdown + auto-downloaded PDF).
- Output language: everything the LLM produces and all UI-facing strings
  (prompts, agent roster, composer headings/references, reviewer
  limitations, SSE progress messages) were just translated to English. The
  cached `CompanyDossier` (`data/dossiers/<cnpj>.json`) and FRE citation
  `section_label`s stay in Portuguese (source-document data) — company's own
  disclosed labels (e.g. "LAJIDA ajustado") may still appear verbatim.
- **Not yet re-validated end-to-end**: no fresh `/1-pager` run since the
  English-output change. Next run should confirm the report is fully in
  English and the confidence score / reviewer error count are unaffected.

## Known issues / improvement ideas (not yet started)

- **Runtime (~10 min per `/1-pager`)**: section generation is sequential
  (`_MAX_CONCURRENT_SECTIONS = 1`, 2s delay between the 9 agents in
  `app/analysis/sections.py`) to avoid Mistral 429s. Possible speed-ups:
  raise concurrency, shrink per-agent Dossier context, cache/skip sections
  unchanged since the last run.
- **Model choice**: `app/config.py:MISTRAL_CHAT_MODEL` is a single global
  constant used by every LLM call in the app (RAG chat, intent/company/year
  extraction, query rewriting, FRE extraction, all 9 analysis agents).
  Swapping it is a one-line change but affects everything; per-pipeline model
  selection would need a second config constant + an optional `model` param
  threaded through `chat_completion()`.
- **Repo size**: `data/cvm_registry.csv` + `data/dfp/*.zip` + `data/itr/*.zip`
  (~90MB) are tracked in git history even though `.gitignore` now excludes
  these patterns (pre-existing, not touched in the latest cleanup). Future
  cleanup: `git rm --cached` (stops tracking going forward) or a full history
  rewrite to shrink `.git` — the latter needs a force-push and care around
  other clones/branches.

## Current priority: 1-Pager Accuracy Improvements

Branch: `feature/credit-committee-template` (merged to `main` 2026-06-17)
Pipeline B code: `app/analysis/committee/`

### Problem statement (found during v1 validation)

Two distinct quality bugs were identified in the generated 1-pager:

1. **Invented calculations** — agents compute YoY growth percentages and ratios
   that are not stated in the dossier (e.g. "revenue grew 12% YoY"). These can
   be wrong due to scale mismatches (MIL vs R$ MM), period mismatches, or pure
   hallucination. Root cause: agent prompts say "be specific and quantified" →
   the LLM obliges by computing numbers itself.

2. **Fabricated facts** — agents write facts about the company that are not in
   the dossier at all, drawn from LLM parametric knowledge (training data).
   Root cause: no hard grounding rule forces the model to stay inside the
   dossier context.

**Design principle going forward: LLMs narrate, they don't compute.**
Pre-calculate every number we want deterministically from the dossier; give
those as typed inputs to the agent; the agent's job is narration only.

### Improvement roadmap (ordered by impact)

**Phase A — Prompt hardening (quick win, highest impact)**
- Files: `app/analysis/committee/agents.py` (all 4 system prompts)
- Add to every agent system prompt:
  - "Every fact and number you write MUST appear verbatim in the Dossier
    provided. Do NOT compute derived metrics (YoY growth %, ratios) unless
    the exact figure is stated in the FRE or disclosed metrics. If a number
    is not in the Dossier, write 'informação não disponível' rather than
    estimating."
  - "Do NOT draw on general knowledge about this company. Only use the
    Dossier context below."

**Phase B — Pre-computed FactSheet (eliminates calculation errors at root)**
- New file: `app/analysis/committee/fact_sheet.py`
- Before agents run, deterministically compute from the dossier:
  - YoY revenue change (absolute + %) — from account 3.01 across periods
  - EBITDA margin trend (most recent vs prior year) — from disclosed metrics
  - Net debt trajectory (change in R$ MM)
  - Leverage trajectory (current vs prior year multiple)
  - Most recent period label + comparison period label
- Pass this typed `FactSheet` (Pydantic model) to agents alongside the
  dossier context. Agents are instructed to use these pre-computed values
  rather than computing their own.
- This removes the entire class of "wrong arithmetic" errors.

**Phase C — Per-bullet source labels (grounding enforcement)**
- Change agent JSON output schema to require each bullet to carry a
  `source` field: the FRE section number or statement type it came from
  (e.g. `"2.1"`, `"DRE_con"`, `"disclosed_metrics"`).
- Post-processing: bullets with `source: null` or `source: ""` are flagged
  or removed before the template is composed.
- Adds lightweight citation trail without full Pipeline A citation overhead.

**Phase D — Output number cross-check (safety net)**
- After composing the PT markdown, extract all numeric values from the text.
- Cross-check each against the dossier (financial_line_items + disclosed_metrics).
- Log any number that cannot be matched — surface as a warning in the SSE
  stream ("⚠ 3 numbers in the report could not be verified against the dossier").
- Does not block generation; gives the analyst a signal to manually check.

### Implementation order

| # | Phase | Effort | Unblocks |
|---|---|---|---|
| 1 | Phase A — prompt hardening | ~1h | immediate quality lift, no schema change |
| 2 | Phase B — FactSheet | ~3h | eliminates arithmetic errors |
| 3 | Phase C — source labels | ~2h | citation trail for analyst review |
| 4 | Phase D — number cross-check | ~2h | safety net, surfaces remaining issues |

Start with Phase A (zero schema changes, immediate impact). Validate with a
fresh Petrobras + Vale run before moving to Phase B.

---

## Credit Committee 1-Pager (v1 — shipped 2026-06-17)

Branch: `feature/credit-committee-template` (merged to `main`)
Architecture guide: `docs/committee-1pager-architecture.md`

v1 is complete and on `main`. The accuracy improvement roadmap above is the
active next task. Pipeline A (9 agents → full memo) is untouched.

**What auto-fills from public data:**
- Financial table "Realizado" column (Faturamento, EBITDA, Margem, DL, Alavancagem)
  extracted from CVM financial statements and FRE 2.5 disclosed metrics.
- Framing paragraph, Grau de preocupação, Próximos passos — LLM-generated
  from FRE 4.1 + sector playbook + Yahoo market data + BCB macro.
- Ratings — best-effort extraction from FRE text, else `[PREENCHER]`.
- 3 narrative sections (Highlights Consolidado, Highlights Holding, Perspectivas).

**What stays as `[PREENCHER]` (internal bank data):**
- Limite, Risco, Run-off %, Share [Banco], Último comitê, Projetado CS/CT.

## Other improvement backlog

- Runtime speedup (raise `_MAX_CONCURRENT_SECTIONS`, shrink per-agent context)
- Model choice (per-pipeline config constant)
- Remove ~90MB CVM data files from git history (`git rm --cached` + rewrite)
