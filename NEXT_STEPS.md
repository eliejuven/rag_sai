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

## Current priority: Credit Committee 1-Pager

Branch: `feature/credit-committee-template` (off `main`)
Architecture guide: `docs/committee-1pager-architecture.md`

**Goal**: `/1-pager <empresa>` produces a filled credit committee 1-pager
matching the Itaú internal template format, in Portuguese (primary) + English
(translated), with two versions saved per run.

**What auto-fills from public data:**
- Financial table "Realizado" column (Faturamento, EBITDA, Margem, DL, Alavancagem)
  extracted from CVM financial statements and FRE 2.5 disclosed metrics.
- Framing paragraph, Grau de preocupação, Próximos passos — LLM-generated
  from FRE 4.1 (risk factors) + sector playbook + Yahoo market data + BCB macro.
- Ratings — best-effort extraction from FRE text, else `[PREENCHER]`.
- 3 narrative sections (Highlights Consolidado, Highlights Holding, Perspectivas)
  — 4 purpose-built agents replacing the 9-agent memo pipeline for this output.

**What stays as `[PREENCHER]` (internal bank data, never public):**
- Limite, Risco, Run-off %, Share [Banco], Último comitê, Projetado CS/CT
  — analyst provides via `PUT /committee/{cnpj}/bank-context`, cached per CNPJ.

**Pipeline A (9 agents → full memo) is untouched.** `POST /analysis/generate`
still works. Only the `/1-pager` UI command is rerouted to the new endpoint.

## Other improvement backlog

- Runtime speedup (raise `_MAX_CONCURRENT_SECTIONS`, shrink per-agent context)
- Model choice (per-pipeline config constant)
- Remove ~90MB CVM data files from git history (`git rm --cached` + rewrite)
