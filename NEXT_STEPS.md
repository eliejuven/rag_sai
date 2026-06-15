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

## Improvement backlog (TBD)

Open — to be filled in as concrete next steps are picked.
