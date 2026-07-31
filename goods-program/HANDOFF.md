# Handoff — B2B Product Matching, next phase

Written 28 July 2026, at the end of the "v2 matching engine" work.
Updated same day, second pass: Tasks 1–3 below are now done. See each
section for what actually shipped versus what was originally planned.
Updated again same day, third pass: **section 10 has an unresolved bug
(LLM auto-match not confirmed working) and real findings from an
independent evaluation against the actual files — read that section
first if you're picking this up fresh.**
Read this together with `UPGRADE_V2.md` (what was broken and why) before
changing anything.

---

## 0. TL;DR for whoever picks this up

The matching engine was rebuilt and verified end-to-end against the real
files. It works. Three things were planned next, in this order, and all
three are now done (28 July 2026, second pass):

1. **Persistent, versioned master catalog** — done. `CatalogVersion` +
   `matches.catalog_version_id` shipped and migrated onto the real DB. See
   section 4.
2. **Attribute extraction / standardisation** — done, but half-scoped
   *deliberately*. Extraction/storage shipped; wiring it into `scoring.py`
   as a fifth signal was explicitly **rejected** after running the
   calibration benchmark — it made top-1/top-3 accuracy worse at every
   weight tested. See section 5 for the numbers and why.
3. **Gemini Flash-Lite as a tie-breaker** — done. Also found and fixed a
   real bug along the way: the LLM reranker path was fully implemented and
   unit-tested but never actually wired into `build_reranker()` — every
   config setting for it was dead. See section 7.

**Do not re-tune the scoring weights or thresholds without re-running the
benchmark in section 6.** They were calibrated against measured score
distributions, and the previous values silently discarded half the data.
This is exactly the discipline that caught (2) above being a bad idea in
its current form — trust the measurement over the intuition that "more
signals should help."

---

## 1. Current state

| | |
|---|---|
| Backend | FastAPI + SQLAlchemy 2.x, `backend/app` |
| Frontend | Next.js 14 (`frontend/`), plus a plain HTML wizard in `frontend/public/matching/` |
| DB | PostgreSQL 16 **in Docker**, host port **5433** |
| Vector DB | Qdrant, port 6333 |
| Tests | 189 passing, 2 skipped (`docker compose exec backend pytest -q`) — skips are the cross-encoder and Gemini import-guard tests, expected since those optional deps aren't installed |
| Status | Full run verified: catalog upload → match → review → skip → save → Excel export. Tasks 1–3 (sections 4, 5, 7) done. |

Everything runs via `docker compose up --build` from the repo root.

### Ports — read this carefully

There are **two PostgreSQL servers** on this machine:

- **Port 5432** — a *native Windows* PostgreSQL 18 install
  (`C:/Program Files/PostgreSQL/18/data`, password `1234`). Holds the
  user's unrelated databases `OOP` and `teacher_survey_mvp`. It also has
  an **empty, unused** `product_matching` left over from debugging — it
  can be dropped.
- **Port 5433** — the Docker container `product_matching_db`
  (password `postgres`). **This is the live one.**

`docker-compose.yml` overrides `DATABASE_URL` and `QDRANT_URL` for the
backend container to container-network addresses (`@postgres:5432`,
`http://qdrant:6333`). The values in `backend/.env` are therefore used
only by host-side tooling (`scripts/check_db.py`,
`scripts/migrate_add_projects.py`) and deliberately point at
`localhost:5433` so those tools inspect the same database the app uses.

### Embeddings are currently TF-IDF, not LaBSE

`EMBEDDING_PROVIDER=tfidf`. The LaBSE install is commented out in
`backend/Dockerfile` because it pulled torch (~2.5 GB) plus a 1.8 GB model
and made the image build take 20+ minutes.

To re-enable: uncomment the two `RUN` lines in `backend/Dockerfile`, set
`EMBEDDING_PROVIDER=sentence-transformers` in `backend/.env`, rebuild.
`build_embedding_provider()` falls back to TF-IDF with a warning if the
model can't load, so this can never break startup.

What TF-IDF costs: no cross-language Kazakh↔Russian matching. ~12% of the
real destination file is Kazakh — but only 0.3% of the catalog is, so most
of those items have no catalog equivalent at all and should end up as "no
match" regardless. Everything else in the v2 engine is pure Python and
unaffected.

---

## 2. Environment gotchas that will waste your time

**`backend/.env` must stay pure ASCII.** pydantic-settings reads it as
UTF-8, but editors on this Russian-locale Windows save as cp1251. A single
em dash in a comment produced
`'utf-8' codec can't decode byte 0xc2 in position 61` and took down the
whole app with an error that named neither the file nor the cause.
`config.py` now pins `env_file_encoding="utf-8"` and raises an actionable
message, but keep the file ASCII anyway.

**PostgreSQL error messages arrive in Russian (cp1251)** and psycopg2
fails to decode them, hiding the real error behind a codec error.
`app/database.py` sets `LC_ALL=C`, `LC_MESSAGES=C`, `PGCLIENTENCODING=UTF8`
and passes `client_encoding=utf8`. If you ever see a bare codec error
again, run `python -m scripts.check_db` — it decodes the underlying bytes
through cp1251/cp866 and identifies which server answered.

**`Base.metadata.create_all` creates missing TABLES but never ALTERs an
existing one.** Adding a column to an existing table needs an explicit
migration — see `backend/scripts/migrate_add_projects.py` for the pattern
(idempotent, additive, works on both SQLite and PostgreSQL). This bit us
once already: the Docker DB kept an old `uploads` table and every request
failed with `column uploads.project_id does not exist`.

Migrations must be run **inside the container**, since that is the only
place `DATABASE_URL` points at the Docker database:

```bash
docker compose exec backend python -m scripts.migrate_add_projects
```

**This folder is not a git repository.** Deletions are permanent. Do not
`rm` anything without asking.

**If you are running in a sandboxed environment (Cowork/Claude Code):**
reading project files through the Linux mount with `cat`/`grep`/`wc`
frequently returns *truncated* content — files appear to end mid-token and
`py_compile` reports bogus syntax errors. The files on disk are fine. Use
the `Read` tool for anything you need to trust, and don't "fix" a syntax
error you only saw through bash.

> **Do NOT transcribe the backend file-by-file to work around this.**
> The Docker container bind-mounts `./backend:/app` straight from the
> Windows filesystem, so it sees the real, untruncated files. Run tests
> and scripts *inside the container* and the whole problem disappears:
>
> ```bash
> docker compose exec backend pytest -q
> docker compose exec backend python -m scripts.migrate_add_projects
> docker compose exec backend python -c "import app.main"   # import check
> ```
>
> `pytest==8.3.4` is already in `requirements.txt`, so nothing extra needs
> installing. This is the correct way to verify anything Python in this
> project. Copying files through Read/Write costs ~85 tool calls and
> introduces transcription risk for zero benefit.

---

## 3. What v2 changed (do not undo these)

Full detail with measured numbers is in `UPGRADE_V2.md`. The short version:

- **`scoring.py` weights now sum to exactly 1.0** and cover only implemented
  signals. Previously three of six signals were never implemented but still
  held 25% of the weight, capping `final_score` at 0.75 while the threshold
  was 0.95 — so hybrid auto-accept had literally never fired. There is an
  `assert` at import guarding this.
- **`keyword_search.py` uses absolute (saturating) BM25 normalisation.** It
  used to divide by the best hit per query, so the top result always scored
  1.0 no matter how bad. Real example: "Обучающие плакаты для дошкольников"
  scored 0.93 against "Оборудование для единоборств", sharing only the
  stopword «для».
- **`lexical_overlap.py` is new and is the most decisive signal.** It answers
  "what fraction of the query's IDF mass does this candidate actually
  contain?" — the question none of BM25/fuzzy/embedding could answer.
- **`fuzzy_search.py` uses `token_set_ratio` + `WRatio`.** `token_sort_ratio`
  scored "манеж детский" vs "манеж детский размерами 830х680 мм" at 55%,
  pushing the verbatim-containing catalog entry out of the top 3.
- **`normalizer.py`** actually strips punctuation now (it previously only
  claimed to), handles Kazakh letters `әғқңөұүһі`, and unifies dimension
  separators (`900*900` = `900х900` = `900x900`).
- **Auto-reject is OFF.** The old `.env` had it on at 0.40 while scores
  capped at 0.75, silently discarding **52.7%** of a 300-row sample before a
  human ever saw it. This is the single biggest reason the two spreadsheets
  "didn't match".

Measured result: ground-truth top-1 94.1% → **97.1%**; true matches now
score mean 0.978 (p10 0.949) versus 0.449 for non-matches (p90 0.718) — a
clean gap where previously the two distributions almost fully overlapped.

---

## 4. Task 1 — Persistent, versioned master catalog

> ✅ **Done, 28 July 2026.** `CatalogVersion` (`app/models.py`) +
> `matches.catalog_version_id`, migrated via
> `scripts/migrate_add_catalog_versions.py --adopt-existing` against the
> real, non-empty Docker DB. `standalone_matching.run_matching_job` now
> accepts a `catalog_version_id` and skips `ingest_master()`/`index.build()`
> entirely on a cache hit (`index_manager.py`'s new per-version cache,
> separate from the main app's global singleton). The wizard
> (`frontend/public/matching/`) has a "use existing catalog" picker
> (`GET /api/v1/matching/catalogs`). Also added
> `scripts/cleanup_orphaned_master_products.py` (dry-run by default) for
> the ~4x duplicate `master_products` rows historical runs left behind —
> not run yet, still there to clean up when convenient.

### The problem

`standalone_matching.run_matching_job()` calls `ingest_master()` and
`index.build()` on **every** wizard run (lines ~250–263). That re-parses
5,163 rows and rebuilds BM25 + fuzzy + vectors each time, and since rows
are never deleted, `master_products` accumulates a full duplicate copy of
the catalog per run.

`Project.master_upload_id` (added in this phase) is half the solution — a
project already pins one catalog upload.

### Design

Introduce a first-class catalog entity, versioned:

```
CatalogVersion
  id, name (e.g. "КазНИИСА 04.2026"), source_upload_id,
  created_at, is_active, product_count
```

**Matches must reference the catalog version, not just the product.** This
is the one decision that is hard to reverse. When the April catalog is
replaced by May and rows are overwritten in place, every previously
confirmed match silently starts meaning something else — and there is no
way to reconstruct what the reviewer actually approved. Add
`matches.catalog_version_id` now, even if only one version ever exists.

Other pieces:

- Build the search index **once per catalog version** and reuse it. Qdrant
  already persists vectors; BM25 and fuzzy are rebuilt in-process on every
  startup (`main.py::_rebuild_index_if_master_catalog_exists`). Consider
  caching them per version, keyed by `catalog_version_id`.
- Give the wizard a "use existing catalog" path so only the destination
  file is uploaded per run. This is the user-visible win.
- Add a cleanup path for orphaned `master_products` from earlier runs.

Migration required (new table + new column on `matches`). Follow the
`migrate_add_projects.py` pattern and test it against a database that
already has data, not just a fresh one.

---

## 5. Task 2 — Attribute extraction / standardisation

> ✅ **Extraction/storage done, 28 July 2026. Scoring integration explicitly
> NOT done — measured and rejected, not skipped.**
>
> Shipped: `app/services/attributes.py` (dimension/material/unit parsing),
> nullable columns on both tables, `scripts/migrate_add_attributes.py`,
> ingestion wiring, `scripts/backfill_attributes.py` for pre-existing rows
> (20,546 of 22,009 master rows and 4,377 of 8,893 destination rows got at
> least one attribute on the real DB).
>
> `app/services/search/attribute_score.py` implements the comparison signal
> itself, deliberately **not** wired into `scoring.py`. Reason:
> `scripts/benchmark_attribute_score.py` (blends it into `final_score` at
> several weights without touching production code) measured, on the real
> data: top-1 accuracy fell from 80.0% (baseline, weight 0) to 65.7% at
> weight 0.20, monotonically, at every weight in between. The score
> separation didn't meaningfully improve either. This is precisely the
> "if a change narrows the gap, it made things worse even if top-1 went
> up" rule in section 6 — it just also caught a *worse* case, where
> accuracy itself regressed. Root cause suspected: `material_similarity`
> is binary (1.0/0.0) and material extraction is coarse enough that real
> matches disagree on it almost as often as real non-matches agree on it.
>
> **Do not wire `attribute_score` into `ScoringWeights` without first
> either improving the signal (e.g. weight dimensions higher than
> material) and re-running the benchmark, or getting a materially
> different result on a cleaner ground-truth pair** — the 80.0% baseline
> here is well below the 97.1% in section 6, most likely because this
> benchmark run auto-selected "newest master upload" and "newest
> destination upload" independently, which may not be the actual matched
> pair a human would recognize (see the ~4 duplicate catalog uploads note
> in section 4). Re-verify with an explicit
> `--master-upload-id`/`--destination-upload-id` pair before trusting the
> absolute numbers; the relative trend (attribute_score hurting as its
> weight increases) was measured against the same retrieved candidates at
> every weight, so that comparison is sound regardless.

### Why this matters more than it sounds

Destination rows are already stored in the DB (`destination_products`,
with the untouched original in `raw_data`). So "put Excel rows in a table"
is essentially done. The value is in **extracting structured attributes
into typed columns**.

This is the largest remaining accuracy lever. Straight from the real data:

```
Манеж детский размерами 830х680 мм
Манеж детский размерами 840х840х680 мм
Корзина для игрушек размерами 420х840х...
```

The dimensions *are* the discriminator, and right now they are just tokens
in a text blob. v2 normalises the separators but does not parse them as
numbers. Extract width/height/depth and compare numerically and you get
discrimination no amount of text similarity can produce.

### Design

Add nullable columns to both `master_products` and `destination_products`
— e.g. `dim_w_mm`, `dim_h_mm`, `dim_d_mm`, `material`, `unit_normalized`,
`quantity_normalized`. Populate during ingestion.

**Additive only.** Never rewrite `product_name`, and keep `raw_data`
intact. A standardisation step that damages the source is very expensive
to undo later.

Then add an `attribute_score` to the scoring pipeline — comparing parsed
numbers, not strings.

> ⚠️ **Adding a fifth signal means re-normalising the weights.**
> `ScoringWeights.total()` must stay exactly 1.0 (there is an `assert`),
> and `HIGH_CONFIDENCE_THRESHOLD` / `MEDIUM_CONFIDENCE_THRESHOLD` were
> calibrated against the *current* four-signal distribution. Re-run the
> benchmark in section 6 and re-pick the thresholds from the new
> separation. Skipping this is exactly how the original 0.75-ceiling bug
> happened.

---

## 6. How to verify a scoring change

Do not trust intuition on this; the whole v2 rewrite came from measuring.

`pytest` first — 157 tests, including regression tests written
specifically to prevent the old bugs returning
(`test_scoring_calibration.py`, `test_lexical_overlap.py`,
`test_normalizer_kazakh.py`, `test_keyword_search.py`).

Then measure against the real files, `Казниса апрель.xlsx` (master,
sheet "База КазНИИСА 04.2026") and `Детсад.xlsx` (destination, sheet
"Список сводный д.сад"):

**Ground truth.** Destination rows whose normalised name exactly equals a
catalog row's give ~34 known-correct pairs. Report top-1 and top-3
accuracy on those. Current: **97.1% / 100%**.

**Score separation.** This matters more than accuracy, because it is what
makes thresholds meaningful. Compare the score distribution of true
matches against a random sample of the rest. Current: true matches mean
0.978 / p10 0.949; mixed pool mean 0.449 / p90 0.718. If a change narrows
that gap, it made things worse even if top-1 went up.

**Spot-check the known-hard cases.** These were all wrong before v2:

| query | expected behaviour |
|---|---|
| `Стеллаж (открытый)` | matches "Шкаф стеллаж открытый ..." at ~0.91 |
| `Обучающие плакаты для дошкольников` | stays LOW (~0.30) — not in catalog |
| `Ертегілер. Өзіміз оқимыз (3 бөлім)` | stays LOW — Kazakh book, not in catalog |
| `Швабра с платформой` | medium (~0.46) — goes to human review |

Practical note: building the index over 5,163 rows takes ~11 s, and a
single query ~0.1 s. Running the full benchmark inside a 45-second sandbox
call requires sampling (150–200 rows), not the whole file.

---

## 7. Task 3 — Gemini Flash-Lite as a tie-breaker

> ✅ **Done, 28 July 2026 — and one real bug fixed along the way.**
> `build_reranker()` (`app/services/search/reranking.py`) read
> `enable_llm_reranker_for_hard_cases` and every LLM setting but never
> actually constructed an `LLMReranker` — the class and
> `AnthropicLLMClient` were fully implemented and unit-tested in isolation,
> but dead in the running app regardless of `.env`. Fixed: `build_reranker`
> now wraps the base reranker in `LLMReranker` when configured, called from
> `index_manager.py`; fails open (base reranker) on a missing key or
> unknown provider, never crashes startup.
>
> `GeminiLLMClient` added, same contract as the Anthropic client (prompt-
> building and response-parsing/validation now shared by both via
> `_build_prompt`/`_parse_choice`). New settings: `llm_reranker_provider`
> ("anthropic" | "gemini"), `gemini_api_key`, `gemini_reranker_model`
> (default `gemini-2.5-flash-lite`). Everything still defaults off/empty —
> nothing changes until an API key is set and
> `ENABLE_LLM_RERANKER_FOR_HARD_CASES=true`.
>
> Re-verified the facts below (this doc's "verified July 2026" was over a
> year stale by the time this shipped): Flash-Lite lineup is still 2.5 and
> 3.1 — no 3.5 Flash-Lite. Free tier still Flash/Flash-Lite only. The
> **data privacy point is confirmed, not hypothetical**: free-tier Gemini
> traffic can be used for training and seen by human reviewers; paid tier
> excludes that; EEA/Switzerland/UK get paid-tier terms even on the free
> tier. This has not been enabled against real tenders — that decision
> was left to the user given the privacy point above. Current SDK is
> `google-genai` (`from google import genai`), not the older
> `google-generativeai`.

### The architecture already supports this

`backend/app/services/search/reranking.py` defines an abstract `LLMClient`
with a single method, and `LLMReranker` that wraps a base reranker and
calls the LLM **only** when the top-2 scores are within
`LLM_AMBIGUITY_THRESHOLD`. `AnthropicLLMClient` is the existing
implementation. Adding `GeminiLLMClient` is one class — do not build a
parallel path.

### Constraints (verified July 2026)

Free tier covers Flash and Flash-Lite only (Pro was removed in April
2026). Flash-Lite gives roughly 30 requests/minute and ~1,000–1,500
requests/day, 250k tokens/minute. Check the exact current model name —
the lineup has 2.5 and 3.1 Flash-Lite; "3.5 Flash-Lite" does not appear to
exist.

At 1,214 destination rows, calling the model per item would exhaust the
daily quota and take over an hour. Only call on genuinely ambiguous cases,
and batch several items per request.

### Rules

- **Never let the LLM auto-confirm.** It suggests and explains; the human
  decides. A confidently wrong match is worse than no match, because
  nobody re-checks it.
- **Rerank, never retrieve.** Ask it to choose among the 3–5 candidates
  already retrieved. Never ask it to search 5,163 products.
- Temperature 0, structured output, and validate the returned index is in
  range — the existing `LLMReranker` already does this and falls back to
  the base ranking on any failure.
- **Data privacy is a real question here.** This is government procurement
  data, and free API tiers commonly permit training on submitted content.
  Confirm the terms before this touches real tenders; a paid tier may be
  required purely for the no-training guarantee.

### Worth knowing

There is already a supervised classifier (`app/services/ml/`) that trains
on confirmed matches once 500 review decisions exist
(`GET /api/ml/training-readiness` tracks progress). It learns *this
organisation's* matching conventions from *their* feedback — Gemini has no
idea that КазНИИСА codes mean anything. Do not assume the LLM will beat
it; they complement each other, and the classifier is far cheaper to run.

---

## 8. Known gaps in the Quick Match Wizard

Not blocking, but they will bite on real volume:

- **No resume.** Choices live in browser memory only — no `localStorage`,
  no incremental save. Closing the tab at item 180 of 200 loses everything.
  The "Upload & Review" tab persists each decision immediately. This is the
  most valuable small fix.
- **No manual catalog search.** If none of the 3 candidates fit, the only
  option is "Не подходит". The Review tab has search; the wizard does not.
- **Auto-matched items are never shown.** `reviewQueue` is `needs_review`
  only, so items ≥0.88 pass through unaudited (~4%, mostly exact matches).

---

## 9. Files worth reading first

| file | why |
|---|---|
| `UPGRADE_V2.md` | what was broken, with measured numbers; also a FastAPI explainer |
| `backend/app/services/search/lexical_overlap.py` | the new decisive signal, with worked examples |
| `backend/app/services/search/scoring.py` | why the weights must sum to 1.0 |
| `backend/app/services/search/keyword_search.py` | the BM25 normalisation bug, documented in place |
| `backend/scripts/migrate_add_projects.py` | the migration pattern to copy |
| `backend/scripts/check_db.py` | run this first whenever the DB misbehaves |
| `backend/app/api/projects.py` | the Project layer, plus FastAPI patterns explained in the docstring |
| `backend/app/models.py` (`CatalogVersion`) | why matches reference a catalog version, not just a product - the "hard to reverse" decision |
| ~~`backend/scripts/benchmark_attribute_score.py`~~ | **deleted, 29 July 2026 cleanup pass (section 12)** - it had already done its job (measured and rejected the attribute-comparison signal, section 5) and nothing imported it. The rejection reasoning itself is still fully written up in section 5 above; only the now-unneeded benchmark code was removed. |
| `backend/app/services/search/reranking.py` (`build_reranker`) | the LLM tie-breaker wiring, and the "dead code despite full test coverage" bug it fixed |

Long comments in these files are deliberate: each one records a bug that
was actually hit and measured, so the same mistake doesn't get
reintroduced. Don't strip them.

---

## 10. Third pass (28 July 2026) — LLM auto-match built, NOT confirmed
working, plus real findings from an independent offline evaluation

### 10.1 LLM auto-match — a fourth feature, added after section 7

While reviewing a real wizard result, the user found an obviously-correct
match stuck in manual review at 76.2%: destination "Дозатор для **житкого**
мыла" (typo: жидкого/liquid misspelled) against catalog "Дозатор жидкого
мыла". `LLMReranker` (section 7) can't fix this - it only reorders
already-close candidates and never touches `final_score`, so a single
correct-but-typo'd candidate stays exactly where it was.

Built on top of section 7's work: `LLMAutoMatchConfirmer`
(`reranking.py`) - a **separate**, opt-in exception to "never let the LLM
auto-confirm" (config.py's `enable_llm_auto_match`, independent of the
tie-breaker flag). It asks the LLM to confirm ONE candidate already above
`medium_confidence_threshold` (0.55), and if confirmed, promotes it to
auto-matched tagged `auto_match_source="llm_confirmed"` -> `Match.method
="llm_auto_matched"`, distinct everywhere (DB, export as `AUTO_MATCH_AI`,
wizard `[AI]` badge) from a real exact/threshold match, so it stays
auditable. Full design and guardrails are in that class's docstring.

**Along the way, fixed a real dependency conflict**: `google-genai`
wasn't in `requirements.txt` at all initially (lazy-import only). Once
added, `google-genai>=2.9.0` requires `pydantic>=2.12.5`, which conflicts
with this project's pinned `pydantic==2.10.3` - pinned to `google-genai
==2.8.0` instead (the newest version whose own pydantic floor still fits;
see the comment in `requirements.txt`). Verified with a clean `pip
install --dry-run` before telling the user to rebuild.

### 10.2 RESOLVED: it's a free-tier quota limit, not a bug

Root-caused via the logging added below: every `[llm_auto_match]` attempt
(142 log lines checked, zero successes, zero `declined`) failed with

```
429 RESOURCE_EXHAUSTED - Quota exceeded for metric: generate_content_free_tier_requests,
limit: 20, model: gemini-2.5-flash-lite
```

This project's free-tier quota for `gemini-2.5-flash-lite` is **20
requests/day** - far lower than the ~1,000-1,500/day general research in
section 7 found; that number apparently assumes an established project,
not a fresh one. Every call (including for the known "Дозатор для житкого
мыла" test case, seen twice in the log) hit the quota wall and fell back
to manual review before ever reaching Gemini for a real answer. The code
itself worked exactly as designed - attempted, failed safely, never
crashed - it just never got to execute successfully even once.

**Two real gaps this exposed, not yet fixed:**

1. Section 7's own design notes said to "batch several items per
   request" to conserve quota - never actually implemented. Both
   `LLMReranker` and `LLMAutoMatchConfirmer` make one API call per item.
   At a 20/day cap, this matters a lot more than it looked like it would.
2. Untested: quotas are normally per-model, so a different free-tier
   model (e.g. `gemini-2.5-flash`, non-lite) might have a separate quota
   bucket. Worth trying before assuming the whole free tier is unusable.

**Options going forward**: wait for the daily reset and test with very
few eligible items, move to the paid Gemini tier (also resolves the data
privacy tradeoff from section 7), or try a different model name.

**Debug logging was added but never exercised** - `_build_auto_match_confirmer()`
in `standalone_matching.py` and `LLMAutoMatchConfirmer.confirm()` in
`reranking.py` now `print()`:

```
[llm_auto_match] disabled (ENABLE_LLM_AUTO_MATCH is not true) - skipping for this run
[llm_auto_match] enabled but no usable client for provider '...' (missing API key?) - skipping for this run
[llm_auto_match] active: provider='...' min_score=0.55
[llm_auto_match] CONFIRMED: '...' vs '...' (score 0.xxx)
[llm_auto_match] declined: '...' vs '...' (score 0.xxx)
[llm_auto_match] LLM call failed (...) - falling back to manual review
```

**Next step for whoever picks this up**: run
`docker compose logs backend --tail 0 -f`, trigger the wizard on this
same case, and read which of the five lines above shows up. That
immediately narrows it to one of three causes: (a) `ENABLE_LLM_AUTO_MATCH`
genuinely isn't set in the running container's `.env` (double-check for
typos/wrong file - there was earlier confusion about which `.env` flags
were actually added), (b) the client builds but the API key/provider is
wrong, or (c) the LLM is being called and is genuinely declining or
erroring - in which case log the actual exception text and the exact
prompt/response, because right now nobody has seen either.

### 10.3 Independent evaluation against the real files (no Docker needed)

Since the sandboxed session doing this work has no Docker access, an
independent, standalone reimplementation of the matching engine was built
and run directly against `Казниса апрель.xlsx` / `Детсад.xlsx` - see
`analysis/independent_matching_check.py`. Same weights/thresholds as
`scoring.py` (0.30/0.20/0.20/0.30, HIGH=0.88/LOW=0.20), same general
4-signal idea (BM25, fuzzy, a TF-IDF+SVD embedding approximation, IDF
lexical overlap) - but NOT byte-for-byte the real app: no Qdrant, no real
embedding provider, no reranker/RRF stage, no LLM. Run it with
`python3 analysis/independent_matching_check.py` (needs pandas, openpyxl,
rank-bm25, rapidfuzz, scikit-learn, numpy - not in `backend/requirements.txt`,
this script is intentionally standalone and never imported by the app).

**Result: 109 auto (35 exact + 74 hybrid-threshold, 9.0%) / 1105
needs_review (91.0%) / 0 no_match**, against the app's own real run of
75/972/186 (1,233 total - 19 more than the confirmed 1,214 real
destination rows; unexplained, possibly a slightly different upload).

What this confirmed:

- File stats match every number HANDOFF already documents exactly: 5,163
  named catalog rows, 1,214 named destination rows, 12.8% of destination
  rows contain Kazakh letters vs 0.3% of the catalog, 35 exact
  normalized-name matches (documented ground truth: ~34).
- The known typo case independently scored 0.871 here vs the app's real
  76.2%/76.5% - close enough to confirm this reimplementation isn't
  methodologically divergent from the real engine.

What this found that's worth acting on:

- **The 0-vs-186 no-match gap is explained, and it's informative, not
  just an error in this script.** Traced to the crude TF-IDF+SVD
  embedding giving false credit to Kazakh-vs-Russian pairs with no real
  relationship (e.g. a Kazakh physics book scored 0.48 embedding
  similarity against an unrelated ball pit) - the exact "embedding space
  collapse" failure mode UPGRADE_V2.md already documents for the TF-IDF
  fallback. Since the real deployment also runs the TF-IDF fallback
  (LaBSE is disabled there too - section 1), this class of risk may not
  be fully absent from the real app either, just apparently better
  calibrated there (the real no-match bucket correctly captures these
  Kazakh items). Worth spot-checking a sample of the app's real 186
  no-matches to confirm they're genuinely uncatalogued rather than
  anything else.
- **A concrete false positive was found in the auto-matched (>=0.88)
  bucket**: "Магнитная рыбалка" (a magnetic fishing TOY) matched to
  "Мешалка магнитная" (a magnetic STIRRER, lab/kitchen equipment) at
  0.901 - genuinely different products that share enough letters to fool
  the fuzzy signal. This is real, demonstrated evidence for section 8's
  already-flagged "auto-matched items are never shown/audited" gap - it
  is not a hypothetical risk.

### 10.4 Recommended next steps, in order

1. Decide how to get past the 20/day quota (10.2): wait for reset + test
   small, move to paid tier, or try a different free-tier model name.
2. If sticking with the free tier long-term, implement the batching
   section 7 always intended but never built - grouping several ambiguous/
   medium-confidence items into one API call would stretch a 20/day quota
   much further than one-call-per-item ever can.
3. Build the missing UI from section 8: surface the app's own real
   auto-matched bucket (currently invisible) so a human can spot-check it
   the way 10.3 just did by hand - directly motivated by finding the
   "Магнитная рыбалка"/"Мешалка магнитная" false positive.
4. Once (1) is unblocked, re-run the wizard on the same files and compare
   the real bucket counts against `analysis/independent_matching_check.py`'s
   109/1105/0 as a second data point - not to match exactly, but to see
   whether the auto bucket grew (LLM auto-match working) without new
   false positives showing up in it.

---

## 11. Fourth pass (29 July 2026) — batching shipped for LLM auto-match;
NOT yet verified in Docker

### 11.1 Decision on 10.4 item 1

User's call (cost/privacy tradeoff, not an engineering one): stay on the
**free tier for now**, batched. Paid tier (which also resolves section 7's
data-privacy tradeoff) remains an option once ready to run this against
real tenders. Trying a different free-tier model name (`gemini-2.5-flash`)
was treated as a low-value diagnostic, not a real fix, and was not done -
non-lite models more often have LOWER free-tier daily caps, not higher.

### 11.2 Batching shipped (10.4 item 2)

Both `LLMReranker` and `LLMAutoMatchConfirmer` made one API call per row -
fatal against a 20/day quota. Shipped:

- `config.py`: new `llm_batch_size: int = 10`. Also corrected the stale
  "~1,500 requests/day" comment on `gemini_reranker_model` to point at
  section 10.2's measured 20/day figure instead.
- `reranking.py`: `LLMClient.confirm_batch()` is now a concrete method
  (not abstract) with a naive one-call-per-pair default, so existing test
  doubles that only implement `pick_best_candidate` keep working unchanged.
  `AnthropicLLMClient` and `GeminiLLMClient` both override it with a real
  single-call batch implementation - `_build_batch_confirm_prompt` /
  `_parse_batch_confirm` are shared across providers the same way
  `_build_prompt`/`_parse_choice` already were (numbered YES/NO lines,
  matched back by number so a reordered/sparse reply can't misalign
  answers - a missing or ambiguous line defaults to `False`, same
  fail-safe rule as `_parse_choice`).
  `LLMAutoMatchConfirmer.confirm_batch()` applies the `min_score` floor
  BEFORE chunking (quota is never spent asking about implausible
  candidates), chunks at `llm_batch_size`, and fails a chunk closed - not
  open, not a crash - on any exception. Deliberately does NOT fall back to
  one-call-per-item on a chunk failure, since that would burn the rest of
  an already-exhausted quota on calls that will also 429.
  `confirm()` (the original single-item method) is now just
  `confirm_batch()` called with one item, so there's one code path that
  talks to the LLM, not two that can drift apart.
- `standalone_matching.py`: `_classify_destination_product` no longer
  calls the confirmer inline - it returns a new `PendingLLMConfirmation`
  dataclass for any row not already decided by exact/hybrid-threshold
  matching. `build_job_result` now runs in two phases: phase 1 classifies
  every row and collects every `PendingLLMConfirmation` across the WHOLE
  run; phase 2 makes ONE batched `confirm_batch` pass over all of them,
  then finalizes each into `auto_matched` (`llm_confirmed`) /
  `needs_review` / `no_match` using the same score split as before. With
  1,214 real destination rows and a `medium_confidence_threshold`-gated
  eligible set, this turns what used to be one API call per eligible row
  into `ceil(eligible / 10)` calls total. Progress reporting reserves the
  last 10% for phase 2 when a confirmer is configured (100% otherwise,
  unchanged).
- **`LLMReranker`'s per-row call was deliberately NOT batched.** It's
  invoked deep inside `matching.get_top_candidates`'s per-row retrieval
  loop, gated by `llm_ambiguity_threshold` (very tight for the default RRF
  base reranker - `0.0005`), so its real-world call volume is far lower
  and batching it means restructuring retrieval into two passes, a
  materially bigger change. If a future log-reading session finds this
  path (not `LLMAutoMatchConfirmer`) burning quota, it needs the same
  collect-then-batch treatment - not done here.

**Tests**: `test_reranking.py` gained direct tests for
`_build_batch_confirm_prompt`/`_parse_batch_confirm` (pure functions, no
network - same convention as `_parse_choice`) and for
`LLMAutoMatchConfirmer.confirm_batch`'s floor/chunking/fail-safe behavior,
including one that asserts N eligible items produce exactly ONE
underlying call and one that asserts chunking at `llm_batch_size` produces
multiple calls of the right sizes. `test_standalone_matching.py`'s old
`test_classify_llm_confirms_a_below_threshold_candidate_as_auto_matched` /
`..._llm_decline_falls_through_to_needs_review` - which called
`_classify_destination_product(..., confirmer=...)` directly - no longer
match the real signature and were replaced with tests covering
`PendingLLMConfirmation` deferral and `build_job_result`'s phase-2
finalization, including one that asserts a single `confirm_batch` call
covers multiple pending rows in one run.

### 11.3 NOT verified - no Docker access in the session that made this change

Same constraint as section 10.3: this session has no Docker access, and
the bash-mounted view of these files truncates well before the real
end-of-file (confirmed again here - `reranking.py` reads as 8,216 bytes
over the sandbox's Linux mount vs. its real, much longer length via the
`Read` tool; see section 2's existing warning). Every edit here was made
and re-read through the `Read`/`Edit` tools directly - never executed in
this sandbox.

**Whoever picks this up next should, inside the container:**

```bash
docker compose exec backend pytest -q
```

then a real check against the known typo case, watching
`docker compose logs backend --tail 0 -f` while the wizard runs against
`Детсад.xlsx`/`Казниса апрель.xlsx` again. Confirm:

1. A single `[llm_auto_match] confirming N candidate(s) via LLM, batched
   10 per call` line appears - not N separate confirm attempts.
2. The `Дозатор для житкого мыла` / `Дозатор жидкого мыла` case comes back
   `CONFIRMED`, not `declined` and not a 429.
3. With `llm_batch_size=10` and a 20/day quota, a run with up to ~200
   eligible candidates (20 calls × 10/call) should now fit in a day where
   it previously exhausted the quota after ~20 candidates total, full
   stop.

If it still 429s: check the actual Google Cloud console quota page rather
than trusting any number in this document - the ~1,500/day figure in
section 7 turned out to be wrong for this project, so a fresh number
should be measured, not assumed, again.

### 11.4 Auto-matched bucket audit UI (10.4 item, motivated by 10.3's false
positive)

**Correction to section 8**: the auto-matched bucket was NOT actually
invisible - `frontend/public/matching/app.js` (the real wizard - the only
one left after the section 12 cleanup pass; `matching-standalone.html` and
`frontend-share/` were older/unused duplicate copies, since deleted)
already had an "авто N предосмотр" button and a read-only detail list,
including an `[AI]` badge
for `llm_confirmed` rows, built sometime during section 10's work. What
was actually still missing, and what section 10.3's false positive
("Магнитная рыбалка" -> "Мешалка магнитная", 0.901) proves matters: the
list was **view-only** - a human could see a wrong auto-match but had no
way to act on it from inside the app. 10.3 only caught it by re-running
the scoring logic offline by hand.

Shipped in `app.js`/`styles.css`:

- Every "авто" detail-view row now gets an **"Отклонить (нет совпадения)"**
  button. Clicking it flags that `destination_id` in a new
  `autoMatchOverrides` Map, re-renders the row with a distinct
  "ОТКЛОНЕНО ПРОВЕРЯЮЩИМ" badge and a tinted background, and swaps the
  button for "Вернуть в «авто»" (undo). `buildSaveRows()` checks this map
  before writing an auto_matched record - an overridden row is sent as a
  real `"без совпадения"` decision instead of `"авто"`, using the exact
  same decision string (and therefore the exact same, already-tested
  backend contract) as a genuine no-match row. **No backend change was
  needed for this** - `standalone_matching.save_results` already handles
  `"без совпадения"` correctly regardless of which UI path produced it.
- Every auto-matched row's top candidate now also gets a passive
  **"⚠ низкое совпадение слов - проверьте вручную"** warning when its
  `coverage` (the `lexical_overlap_score` already sent to the frontend via
  `_candidate_json`) is below `0.3` - this is precisely the signal that
  would have flagged the "Магнитная рыбалка" case: a high fuzzy-string
  score from shared letters, almost no real shared words. It's a hint, not
  a re-classification - exact matches always report `coverage: 1.0` and
  never trigger it, so most auto-matches are unaffected.
- The "авто N предосмотр" summary button and the detail view's own title
  both show a running "(отклонено: N)" count once anything's been
  overridden, so a reviewer who goes back to the summary screen can see
  that a rejection actually took effect without re-opening the detail view.
- `autoMatchOverrides` is cleared in `startWizard()` alongside
  `userChoices`/`skippedItems`, so a stale override can't leak from a
  previous run into a fresh one.

**Not done / next steps for whoever picks this up:**

- No backend/pytest coverage was added for this - it's pure frontend logic
  and `standalone_matching.save_results`'s handling of `"без совпадения"`
  was already locked in by existing tests
  (`test_save_results_marks_no_match_for_rejected_decision`,
  `test_build_export_workbook_...`). No JS test harness exists in this
  project for `app.js` - manual verification (open the wizard, run a
  match, reject an auto-matched item, save, confirm the exported row reads
  `NO_MATCH` not `AUTO_MATCH`) is the only check that's actually been done
  here, and even that was reasoned through, not executed, since this
  session has no browser/Docker access either.
- The `0.3` suspicious-coverage threshold is a guess, not a measured
  value - nobody has checked what `coverage` the real "Магнитная
  рыбалка"/"Мешалка магнитная" pair actually scored (10.3's script logged
  the final blended score, 0.901, not the individual signal). Worth
  pulling that number from `analysis/independent_matching_check.py`'s
  intermediate output (or re-running it) and re-calibrating this constant
  against it rather than trusting the placeholder here.
- This only lets a reviewer reject an auto-match to `"без совпадения"` -
  it does not let them redirect it to a *different* catalog candidate
  (that still requires the manual wizard). Rejecting and then finding the
  right match some other way (e.g. a future run) is the only path today.

---

## 12. Fifth pass (29 July 2026) - a real save-blocking bug, a real cross-
check of the independent script against the real app, and a cleanup pass

### 12.1 Bug: saving was blocked entirely unless every item was reviewed

Real user report: after running a match and skipping the remainder of
manual review, the wizard refused to save/export at all, showing "Не все
позиции проверены. Вернитесь к ручному выбору." - even though "Пропустить
остальные" is an explicit, supported action.

Root cause in `frontend/public/matching/app.js`'s `saveResults()`: the
completeness check was `userChoices.get(id) === undefined`, with no
exception for `skippedItems` - unlike the older `matching-standalone.html`
copy, which already had that exception. A skipped item never gets a
`userChoices` entry, so the guard blocked the whole export the moment
anything was skipped.

Fixed by removing the blocking check entirely (a reviewer must be able to
export progress at any point, not just after finishing every item) and
fixing a second, related bug it would otherwise have exposed:
`buildSaveRows()` could not tell "explicitly rejected via Не подходит"
(`userChoices.get(id) === null`) apart from "never opened in the wizard at
all" (`userChoices.get(id) === undefined`) - both are equally falsy under
a `chosen ? ... : ...` check. Now uses `userChoices.has(id)` so a
never-visited item is exported as `"пропущено"` (stays pending, same as an
explicit skip) rather than silently persisted as `"отклонено"` (a real
no-match verdict the user never actually made). No backend change was
needed - `"пропущено"` was already a fully supported, tested decision
type; it just wasn't reachable in this situation before.

### 12.2 Cross-check: `matching (3).xlsx` (real app export) vs
`independent_matching_evaluation.xlsx` (the section 10.3 script's results)

Joined both files on destination item name (1199 of 1200 real-export names
matched). Two real findings, one of which **corrects section 10.3's own
framing**:

- **"Магнитная рыбалка" is not actually a live risk in the real app.** The
  independent script auto-matched it to "Мешалка магнитная" at 0.9012. The
  real app, on the same item, scores it only **67%** - safely below its
  0.88 auto-match bar, sitting in review, never auto-confirmed. The false
  positive was real, but specific to the standalone script's cruder
  scoring approximation, not a demonstrated bug in the running app. Section
  10.3's "concrete evidence" framing for this specific pair should be read
  with that correction; the audit UI built in section 11.4 is still good
  ("Не подходит" rate is not zero and won't be), just not proven necessary
  by *this* example the way it was originally written up.
- **A stronger, systemic version of the same failure mode WAS found**,
  though: 32 unrelated destination rows - mostly Kazakh/Russian/English
  trilingual book titles ("Клеопатра", "Наполеон", "Нельсон") - all got
  pulled toward one unrelated catalog item, a wireless voltage sensor with
  a long trilingual description ("Датчик напряжения... DataHarvest
  1131..."), purely because both texts are long multilingual strings. 3 of
  those 32 crossed the independent script's own 0.88 line - book titles
  auto-matched to a lab sensor. This is exactly the "embedding space
  collapse" bug UPGRADE_V2.md documents for the TF-IDF fallback (LaBSE is
  disabled in the real deployment too - section 1) - this cross-check just
  gives it three concrete, reproducible instances. None of the 32 crossed
  the real app's own threshold in this run, but the underlying risk (a
  degraded/fallback embedding provider inflating cross-language
  similarity) is architectural, not specific to the standalone script.
- Smaller, opposite-direction finding: 7 items the real app **did**
  auto-match (89-96%) scored just under the independent script's own bar
  (0.81-0.88) - all look like genuinely correct matches (Seca scale,
  electric kettle, industrial vacuum, etc.), so this direction looks like
  the independent script under-scoring, not the real app over-matching.
  One exception worth noting: for "Фитбол" the two approaches picked
  *different* catalog size variants for the same query (65cm vs 85cm ball)
  - a reminder that "which candidate is right" and "how confident to be"
  are separate questions this comparison can't resolve on its own.

**Context this cross-check does NOT establish**: the real export
(`matching (3).xlsx`) came from the same test run described in section 11
- 971 of 1233 rows are `SKIPPED` (still pending, unreviewed - saved via the
12.1 fix above, not because they were actually decided). "The real app
left 971 items in review" is a statement about that one run's LLM-auto-
match quota being exhausted (section 11.3), not a general accuracy claim.

### 12.3 Cleanup pass

User's framing: LLM auto-match is quota-limited and, for now, not the
product's actual value - the value is the retrieval+scoring+human-review
loop, and human review remaining central is a deliberate safety property,
not a stopgap. Asked before deleting anything (this folder has no git;
deletions are permanent) and confirmed exact scope. Removed:

- `frontend/public/matching-standalone.html` and the entire
  `frontend-share/` folder - confirmed, by tracing the Next.js app's own
  iframe (`app/page.tsx`'s `WIZARD_SRC`), that neither is used anywhere;
  both were older/duplicate copies of the wizard that had already caused
  real confusion once this session (looking like the fix in 12.1 "wasn't
  taking effect" when it was actually just a different, stale file).
- `backend/app/services/search/attribute_score.py`,
  `backend/scripts/benchmark_attribute_score.py`, and
  `backend/tests/test_attribute_score.py` - confirmed via grep across the
  whole backend that nothing outside themselves imported any of these
  (the benchmark had already done its one job: measuring and rejecting the
  attribute-comparison signal, section 5). `backfill_attributes.py`'s
  docstring referenced the now-deleted benchmark script by name; updated
  rather than left dangling.

**Deliberately NOT deleted, despite the broader "attribute-scoring code"
request**: `backend/app/services/attributes.py`,
`backend/scripts/migrate_add_attributes.py`, and
`backend/scripts/backfill_attributes.py`. Verified via a dedicated
subagent trace that `attributes.py` is imported and called by
`ingestion.py` on every real upload - deleting it would have broken the
live ingestion pipeline, not removed dead code. Only the *scoring signal*
(comparing extracted attributes between candidates) was ever rejected;
extraction and storage into `dim_w_mm`/`dim_h_mm`/`dim_d_mm`/`material`/
`unit_normalized`/`quantity_normalized` remain live, used, and populated
with real backfilled data (20,546 of 22,009 master rows, section 5) that
would be expensive to regenerate. This is the distinction to keep in mind
if "delete the attribute stuff" comes up again: extraction/storage is
live; only the never-wired scoring signal was dead.

**LLM auto-match/reranker code (sections 7, 10, 11) was explicitly left
untouched** per the user's own choice - still fully implemented, still off
by default via `enable_llm_auto_match`/`enable_llm_reranker_for_hard_cases`,
available to revisit if the paid-tier question from section 11.1 is
reopened.

---

## 13. Sixth pass (29 July 2026) - new "Catalog" management tab: view, edit,
soft-delete master catalog rows directly

### 13.1 What this is and why

User request: a new tab, alongside "Upload & Review" / "Stats & Training" /
"Quick Match Wizard", to manage the master catalog table directly -
view, edit, and delete rows - rather than only ever re-uploading a whole
replacement Excel file.

Before writing any code, three real architectural questions were surfaced
and confirmed with the user rather than assumed:

1. **Deletion semantics.** `Match.master_product_id`,
   `MatchCandidate.master_product_id`, and
   `Feedback.selected_master_product_id` all reference `master_products.id`
   with no `ON DELETE` rule (confirmed via a dedicated subagent trace,
   grepping every FK in `models.py`) - a hard DELETE on a row still
   referenced by a confirmed match would orphan those rows or raise an
   integrity error depending on the database. **Decision: soft delete** -
   a new `master_products.is_active` column, defaulting to `True`. Deleting
   sets it to `False`; nothing is ever actually removed from the table.
   Matches this project's own existing principle (`projects.py`'s
   `delete_project` docstring: "Removing a grouping should not destroy
   ingested data... if the user really wants the rows gone, that is a
   separate, explicit action").
2. **Catalog scope.** MasterProduct has no direct FK to CatalogVersion -
   only indirectly via `upload_id` -> `Upload` -> whichever
   `CatalogVersion.source_upload_id` points at it. **Decision: scope to
   whichever CatalogVersion is currently active** (`is_active=True`,
   ties broken by newest `created_at` - the same rule
   `list_catalog_versions` already uses), no separate picker UI. Falls
   back to showing every MasterProduct row across every upload,
   unscoped, if no CatalogVersion is active at all (legacy pre-section-4
   data) - a warning banner in the UI says so rather than silently
   including possible duplicates from old runs.
3. **Search index freshness.** `index_manager.py` already had
   `invalidate_cached_index_for_version` with a docstring saying "No
   caller yet" (confirmed zero callers anywhere in the repo, before this
   pass). **Decision: invalidate only, don't rebuild synchronously** - an
   edit/delete now calls that function for the product's CatalogVersion
   (if any), and `standalone_matching.run_matching_job` already treats a
   cache miss as "rebuild automatically." Rebuilding synchronously on
   every edit would make the table feel slow (~11s measured for 5,163
   rows, section 6). **The main "Upload & Review" tab's own global index
   (a separate singleton, not scoped by catalog version) is deliberately
   NOT touched by this feature** - it already has the same
   "stale-until-`POST /api/search/reindex`" behavior for every other kind
   of catalog change (e.g. uploading a brand-new master file doesn't
   auto-reindex it either), so this doesn't introduce a new staleness,
   just leaves the existing one exactly as-is.

### 13.2 What shipped

Backend:

- `models.py`: `MasterProduct.is_active: bool = True`.
- `scripts/migrate_add_master_product_active_flag.py` - additive,
  idempotent, follows the `migrate_add_projects.py` pattern. Must be run
  once against any existing database:
  `docker compose exec backend python -m scripts.migrate_add_master_product_active_flag`
  (backfills every existing row to `True` as part of the same
  `ALTER TABLE ... DEFAULT TRUE`, unlike `migrate_add_projects.py`'s
  nullable column which needed no backfill value at all).
- `loader.load_master_records` and `matching.find_exact_match` both now
  filter `is_active = True` - the two *different* paths that can produce
  a match (the indexed/hybrid path and the direct-query exact-match path,
  see that function's own docstring) each needed their own filter; missing
  either one would let a "deleted" row keep matching silently.
- New router `app/api/catalog.py` (`/api/catalog/products` - list with
  search/pagination/`include_inactive`, get one, PATCH, DELETE, POST
  `/restore`), new schemas in `schemas.py`
  (`MasterProductRead`/`Update`/`ListResponse`), registered in `main.py`.
- **A real correctness bug caught and fixed while building this, not left
  as a caveat**: `normalized_name` (what search/exact-match actually
  compare against) is derived from `product_name`/`description` at
  ingestion time via `normalizer.build_normalized_name`. If the PATCH
  endpoint only updated `product_name`, an edited row would keep matching
  against its OLD name forever. `update_product` now re-derives
  `normalized_name` automatically whenever either source field changes.

Frontend: new `CatalogTab.tsx` (Tailwind, following `StatsTab.tsx`'s
conventions exactly - own `useState`/`useEffect`, functions from
`lib/api.ts`, no external table library). Searchable/paginated table,
inline row editing (Edit -> input fields -> Save/Cancel), Delete with a
confirm dialog and an "Undo" banner (calls `/restore`), a "Show deleted"
toggle that swaps the Delete button for Restore on inactive rows. Added
as a fourth tab ("Catalog") in `page.tsx`.

Tests: `backend/tests/test_catalog_api.py` (new) covers scoping to the
active CatalogVersion, the no-active-version fallback, search (name and
external_id, case-insensitive), pagination, partial-field PATCH,
`normalized_name` re-derivation, soft-delete idempotency and its effect
on `load_master_records`/`find_exact_match`, restore, and that
`invalidate_cached_index_for_version` is (and isn't, on a no-op PATCH)
actually called with the right id.

### 13.3 NOT verified - no Docker or browser access this session, and a
newly-discovered widening of the truncation bug in section 2

Same constraint as every other pass this session: nothing here has
actually been executed. `docker compose exec backend pytest -q` and a
real click-through of the new tab are both still needed.

**New finding, worth folding into section 2's existing warning**: the
bash-mount truncation bug isn't limited to Python/`cat`/`grep` - running
`tsc --noEmit` through Node in this sandbox produced phantom errors
(`JSX fragment has no corresponding closing tag`, etc.) in `page.tsx` and
in `ReviewTab.tsx` (a file untouched this session). Checking
`fs.readFileSync` directly confirmed `page.tsx` truncates to 1,489 bytes
over this sandbox's mount, cutting off mid-JSX barely a third of the way
through the real file (confirmed intact and well-formed via the `Read`
tool, which was used for every actual verification in this pass instead).
**Do not trust a `tsc`/Node compile error produced inside a sandboxed
agent session on this project without cross-checking the flagged file
through a tool that reads the real filesystem** - the same rule section 2
already gives for Python, now confirmed to apply to Node/TypeScript too.

**Next steps for whoever picks this up**:

1. Run the migration, then `pytest -q`, then open the new "Catalog" tab
   and actually click Edit/Delete/Restore against a real catalog.
2. There is no "add a new row" endpoint/UI - only view/edit/delete of
   existing rows, since that's what was asked for. Worth adding if the
   catalog needs entries created outside a full Excel re-upload.
3. `projects.py`'s `_upload_summary` product count still counts
   soft-deleted rows (cosmetic only - it's a per-upload total shown in the
   Projects UI, not used for matching) - left as-is, out of scope for this
   pass, but worth knowing if that count ever looks off after someone
   deletes catalog rows.
4. The frontend's numeric fields (price, dimensions) don't validate
   non-numeric input before sending a PATCH - `Number("abc")` is `NaN`,
   which would round-trip through the API as `null`-like JSON (`NaN` is
   not valid JSON and `JSON.stringify` turns it into `null`) rather than a
   clear client-side error. Minor, not exercised by any test.

---

## 14.0 Confirmed fixed, then a real follow-up fix: migrations now run
automatically on startup

The Catalog tab's "Failed to fetch" (section 13.3) was confirmed to be
exactly the predicted cause: `migrate_add_master_product_active_flag` had
never been run against the real database. Running it by hand fixed it
immediately.

User then asked for this to stop being a manual step at all. `main.py`'s
startup handler now calls every migration script's `migrate()` function
(default args only, never `adopt_existing=True`) before rebuilding the
search index - `init_db()`'s `Base.metadata.create_all()` only ever
creates missing tables, never alters an existing one, so this project has
always needed an explicit migration step after any schema change; every
migration script was already written idempotent specifically so it's safe
to re-run, which is exactly what makes it safe to also run
unconditionally on every startup now. Each one is wrapped independently
(one script's failure can't block the others or stop the app from
starting), matching the same fail-open convention
`_rebuild_index_if_master_catalog_exists` already used. Not yet verified
by an actual container restart when first written - and the user did
restart and still hit "Failed to fetch". Root cause of THAT: the first
version imported all four migration modules up front, outside the
per-migration `try`/`except` - a single import failure (not just a
`migrate()` call failure) would raise straight out of
`_run_pending_migrations`, past `on_startup`, and crash the whole app
before it served a single request. Fixed by moving the import inside each
iteration's own `try` block, so an unimportable script degrades exactly
like a failing one: logged, skipped, the other three (and the app itself)
still run.

**That still didn't fix it - the app kept hanging at "Waiting for
application startup" and the frontend never started on its own,
requiring a manual click every time.** Diagnosed properly this time
instead of guessing a third time:

1. `pg_stat_activity` was checked first (non-destructive) - no locks, no
   blocked queries, nothing `idle in transaction`. This ruled out a stuck
   migration transaction as the cause AT THE MOMENT IT WAS CHECKED.
2. Isolation test: commented out the startup-time migration call only,
   left the index rebuild in place, restarted. The app started cleanly -
   `/health` returned 200 repeatedly, and the frontend reached "Ready" on
   its own without anyone pressing anything. **This confirmed the
   migrations were the actual cause**, contradicting the `pg_stat_activity`
   snapshot - the lock/contention was transient, not present at the exact
   moment it was checked.
3. Real mechanism, now confirmed rather than theorized: `uvicorn --reload`
   fires FastAPI's `@app.on_event("startup")` handler on **every reload**,
   not just a genuine container start - and reloads were happening
   constantly during this exact debugging session (every file edit
   triggers one). A reload's restart signal landing while a migration's
   `with engine.begin(): ALTER TABLE ...` was mid-transaction could leave
   Postgres in a state a subsequent attempt then waits behind - by the
   time `pg_stat_activity` was actually queried, whatever was stuck had
   already cleared, which is exactly why that check came back clean even
   though the cause was real.

**Actual fix, not just a workaround**: migrations no longer run from
inside `app/main.py` at all. New `backend/entrypoint.sh` runs every
migration script (still default args only, never `adopt_existing=True`)
once, then `exec`s `uvicorn` - so they run exactly once per real
container start, and are never re-triggered by a hot-reload again.
`Dockerfile`'s `CMD` now runs this script via `sh entrypoint.sh` rather
than executing it directly, since `docker-compose.yml` bind-mounts
`./backend` over `/app` at runtime and a Windows bind mount doesn't
reliably preserve the Linux executable bit `RUN chmod +x` set at build
time - invoking the interpreter explicitly sidesteps that entirely.
`main.py`'s `on_startup()` is back to just `init_db()` +
`_rebuild_index_if_master_catalog_exists()`, with a comment explaining why
migrations don't belong there.

**Requires a rebuild, not just a restart** - `docker compose up --build`
(the `Dockerfile` itself changed: new `CMD`, new file to copy in). A
plain restart would keep using the old image and old `CMD`.

**Verified**: the rebuild worked exactly as intended - all four
migrations ran, each correctly reported `[skip] already exists`, then
handed off to uvicorn cleanly. The reload-interruption bug is genuinely
fixed.

**But a NEW, still-unresolved symptom surfaced immediately after**: the
app still hangs at "Waiting for application startup", now stuck at (or
after) `_rebuild_index_if_master_catalog_exists()` specifically - the
Qdrant client version-mismatch warning fires (confirming a real, working
network round-trip to Qdrant already happened), then nothing further.
`docker ps`/Docker Desktop shows `backend` running but never healthy, and
`frontend` never starts on its own (`depends_on: condition:
service_healthy` holds it back) - same downstream symptom as every
previous round, different actual location in the code this time.

**A real, separate bug was caught while investigating THIS**: the
expected diagnostic print line
(`"[startup] Found N existing master products - rebuilding search
index..."`) has never once appeared in ANY log this session - not even
during the run that's confirmed to have worked (the migrations-disabled
isolation test, which reached real `/health` 200s). Root cause: Python
buffers stdout when it isn't attached to a real terminal, which `docker
logs` never is - so `print()` output can sit unflushed indefinitely,
while `warnings.warn()` (the Qdrant message) and uvicorn's own logging
module both write to stderr/a logging handler and appear immediately
regardless. `docker-compose.yml` never set `PYTHONUNBUFFERED`, so this
whole debugging session had a real blind spot: the absence of an expected
print line was being read as "hasn't reached that code yet," when it may
already have run and just not been visible. Fixed by adding
`PYTHONUNBUFFERED: "1"` to the backend service's environment.

**Resolved - not a hang at all.** With `PYTHONUNBUFFERED` actually working,
the missing print line finally showed up: `"[startup] Found 99919 existing
master products - rebuilding search index..."`. **99,919** - the real
catalog is ~5,163-5,194 rows, so this was ~19x bigger than anything tested
tonight. Not broken, just legitimately rebuilding a BM25/fuzzy/vector index
over a table 19x the expected size, and it never got the chance to finish
because earlier rounds kept interrupting it (Ctrl+C, reloads) before it
could.

**Root cause of the bloat, found via a throwaway audit script**
(`catalog_audit.py`, listed every master upload with its row count and
exactly why it was being kept/orphaned): 20 master uploads total.

- One real historical oddity, unrelated to tonight: `Детсад.xlsx` (the
  file used as a *destination* file all session) was, at some point,
  ingested as a **master** upload by mistake (1,233 rows) and has a real
  `Match` depending on it - protected, left alone, just worth knowing
  about.
- **15 separate `CatalogVersion` rows, every one marked `is_active=True`,
  all wrapping the same 5,194-row `Казниса апрель.xlsx`** - created across
  repeated testing where "upload a new catalog" was used instead of
  "reuse existing catalog." `CatalogVersion.is_active` was deliberately
  designed to allow more than one active version (models.py's own
  docstring), but nothing was ever designed to expect - or clean up - 15.
  Of those, 2 had real `Match` history (including the newest, actually-
  in-use one) and had to stay; the other **13 had never been matched
  against even once** - pure dead weight.
- Plus 2 fully orphaned uploads `scripts/cleanup_orphaned_master_products.py`
  already knew how to find (that script deliberately treats ANY
  `CatalogVersion` reference as "keep," so it correctly left the 13 stale-
  but-still-referenced ones alone - a different, narrower problem than what
  it was built for).

**New script written for the narrower case**:
`scripts/cleanup_stale_catalog_versions.py` - keeps the single newest
active `CatalogVersion` and anything with real `Match` history, flags
every other active-but-never-matched `CatalogVersion` (and its
`MasterProduct` rows) as safe to delete. Dry-run by default, same
convention as every other cleanup script here. Both scripts were run with
`--execute`: 67,522 + 10,388 = 77,910 rows removed, leaving
`master_products` at ~22,009 - which matches, almost exactly, the figure
already on record in section 5 from the attribute-backfill work, a strong
independent sanity check that this cleanup was correctly scoped and not
an overcorrection.

**Kept as permanent tooling** (moved into `backend/scripts/`):
`cleanup_stale_catalog_versions.py` - this is a structural gap ("upload
new" vs "reuse existing" will keep producing this exact bloat with
continued testing), not a one-off. The other diagnostic
(`catalog_audit.py`) was a throwaway report for this specific
investigation and was deleted once its job was done.

**Not yet independently reconfirmed with a fresh rebuild** at the moment
of writing this - the numbers above are from the cleanup scripts'
own dry-run/execute output, which is trustworthy on its own terms, but
nobody has yet pasted a fresh `docker compose up --build` log showing the
faster startup time end to end.

---

## 14. Seventh pass (29 July 2026) - frontend redesign: sidebar nav, down
to two tabs, wizard restyled

### 14.1 Decision: drop "Upload & Review" and "Stats & Training" from
navigation

User asked to redesign the UI - full scope (structure and visuals), wizard
included. Before touching anything, walked through what each existing tab
actually does (`ReviewTab.tsx` fully read for the first time this session)
to check for real functional overlap before proposing to cut anything:

- **"Upload & Review" (`ReviewTab.tsx`)** is a genuinely different
  workflow from the Quick Match Wizard, not a worse version of it:
  continuous one-item-at-a-time review with keyboard shortcuts
  (1/2/3/N/Enter), a "prioritize by uncertainty" batch action (Phase 8),
  and a persistent, non-versioned global catalog you keep matching
  against indefinitely, with no export step - decisions write straight to
  `Match`/`Feedback` as you go. The wizard is a one-shot batch: upload two
  files (or reuse a `CatalogVersion`), bulk-classify, review only the
  uncertain ones, export an annotated Excel. Confirmed via the user this
  first workflow is never actually used - the wizard already covers the
  real workflow - so it was safe to drop from navigation.
- **"Stats & Training" (`StatsTab.tsx`)** was established two conversation
  turns earlier (see the exchange right before this section) to bundle a
  genuinely useful feedback-stats dashboard with a model-training action
  that doesn't feed anything live - see that discussion for the reasoning.
  Dropped along with the review tab.

**Neither component was deleted.** `ReviewTab.tsx` and `StatsTab.tsx`
still exist on disk, just no longer imported by `page.tsx` - this folder
has no git, so deleting code is a one-way door, while leaving an unused
file costs nothing and is trivially reversible. The backend endpoints
both tabs used (`/api/matching/*`, `/api/ml/*`, `/api/uploads/master`,
`/api/search/reindex`) were also left completely untouched - nothing
about this pass removed backend functionality, only frontend navigation.

### 14.2 What shipped

- `frontend/app/page.tsx` rewritten: horizontal tab strip -> left sidebar
  (`aside`, 208px, two links with small hand-written inline SVG icons -
  no icon library was added; this project intentionally has zero UI-kit
  dependencies beyond Next/React/Tailwind, and two icons don't justify
  one). Default tab is now `"quickmatch"` (the wizard) instead of
  `"review"`, since review no longer exists as a tab. The old
  `isWide`/`max-w-3xl` vs `max-w-6xl` toggle was removed - with only two
  sections, both want full width, so the sidebar layout just always
  gives the content area `flex-1`.
- `CatalogTab.tsx`: the plain-text "active"/"deleted" status became a
  small `StatusBadge` component (green-50/green-800 vs red-50/red-700
  pill), reused in both the read view and the inline-edit view instead of
  being written twice.
- `frontend/public/matching/styles.css` rewritten with CSS custom
  properties (`--accent`, `--border`, `--danger`, etc.) matching the
  Tailwind tokens used everywhere else (blue-600 accent, gray-200/300
  borders, rounded-lg radius), so the iframed wizard no longer looks like
  a visually separate tool bolted onto the app. **Every class name and
  element id `app.js` reads via `classList`/`getElementById` was checked
  against the old file and kept identical** - this was a values-only
  restyle, zero HTML/JS changes, zero risk to the carefully-tuned wizard
  logic documented across sections 7-11.

A rough visual mockup of the sidebar/dashboard direction was shown to the
user via the visualize tool before writing any code, to confirm the
direction before investing in implementation - the shipped version is
simpler than that mockup (no separate "Dashboard" landing page - with
only two sections a landing hub adds indirection rather than removing
it, so the app now loads straight into the wizard).

### 14.3 NOT verified - no browser access this session

Same constraint as every pass this session: nobody has actually opened
this in a browser. Before trusting it:

1. `npm run dev` (or the Docker-composed frontend) and open the app -
   confirm the sidebar renders, both tabs switch correctly, and the
   wizard iframe still loads and functions inside the new layout.
2. Confirm the restyled wizard CSS doesn't clash with anything - in
   particular, `.detail-item:has(.detail-rejected)` depends on `:has()`
   support (Chrome/Edge/Safari 2023+); if this needs to run in an older
   browser, that selector degrades to "no tint," not a crash, but worth
   knowing.
3. `ReviewTab.tsx`/`StatsTab.tsx` are still there, still fully
   functional, still hitting real backend endpoints - if either tab needs
   to come back, it's a one-line re-import + `TABS` entry, not a rebuild.

---

## 15. Ninth pass (30 July 2026) - NEXT_STEPS section 1 confirmed working;
new incremental catalog upsert (April -> May)

### 15.1 Section 1 of NEXT_STEPS.md: confirmed

The user ran `docker compose up --build` and pasted the real log:
`[startup] Found 22009 existing master products` (not 99,919 - section
14.0's cleanup held), startup reached "Application startup complete"
without hanging, and the Catalog tab's PATCH/DELETE calls both returned
200 against real rows. First real end-to-end confirmation this session
that section 13/14's fixes actually work outside a dry run.

`backend/catalog_audit.py` (the throwaway diagnostic script, already
planned for deletion once this was confirmed) could NOT be deleted from
this sandboxed session - `rm`/`os.remove` both returned "Operation not
permitted" on the bind-mounted folder, almost certainly because
`docker compose` was running at the time and Windows held a lock on the
file via the bind mount. Left for the user to delete directly (or for a
future pass once containers are stopped) - not forced, per this folder's
no-git/no-destructive-guessing rule.

### 15.2 New feature, user-requested: incremental catalog upsert

The user flagged that the next real catalog file will be "Казниса май" - a
near-identical revision of the same "Казниса апрель" catalog already
ingested - and asked that uploading it overwrite/add data onto the
existing catalog rather than wholesale-replacing it or removing anything
absent from the new file.

Before writing any code, checked whether the real Excel actually has a
stable key to match rows on, rather than assuming one: the "Код" column
(mapped to `external_id`) is confirmed unique across all 5,026 real
product rows in this project's own `Казниса апрель.xlsx` - zero
collisions. That is what makes an upsert-by-code approach sound rather
than a guess.

Shipped:

- `MasterProduct.updated_at` (nullable, NULL = "never merged" - same
  "honest NULL over a guessed value" convention as the attribute columns)
  + `scripts/migrate_add_master_product_updated_at.py` (idempotent,
  additive, same pattern as every other migration here) + wired into
  `entrypoint.sh` alongside the other four migrations.
- `app/services/catalog_merge.py` - `merge_master_file_into_version()`.
  Ingests the new file exactly like any other master upload (reusing
  `ingest_master()` unchanged - column mapping, attribute extraction,
  per-row error handling all apply identically), into a throwaway staging
  Upload, then folds it into the TARGET CatalogVersion's existing rows:
  matched by `external_id` (falling back to `normalized_name` for the rare
  row with no code) rows are updated **in place - same MasterProduct.id**,
  which is the whole point: `Match.master_product_id` /
  `MatchCandidate.master_product_id` /
  `Feedback.selected_master_product_id` all reference a specific row's id
  (CatalogVersion's own docstring already explains why that's deliberate),
  so a brand-new-CatalogVersion-every-month approach would silently strand
  every confirmed match's history. Genuinely new codes get a new row,
  re-parented onto the target's `upload_id` (every retrieval path scopes by
  a single `upload_id`, not by CatalogVersion, so a row left under the
  staging upload would be invisible to search/matching/the Catalog tab).
  Rows in the old catalog simply absent from the new file are **left
  completely untouched** - not soft-deleted, not flagged - per the user's
  explicit "don't completely remove old one." A previously soft-deleted row
  that reappears in the new file is reactivated (`is_active` back to
  `True`) - the file is treated as the source of truth for what currently
  exists. `CatalogVersion.product_count` is recomputed by a real count
  query at the end, not incremented by arithmetic, to avoid drift. Commits
  once, at the end - a half-merged catalog would be worse than the whole
  attempt failing and leaving the catalog exactly as it was before.
- `POST /api/catalog/versions/{catalog_version_id}/update-from-file`
  (`app/api/catalog.py`) - multipart upload, 404s on an unknown version id,
  returns a `CatalogMergeResult` (`updated`/`reactivated`/`inserted`/
  `unmatched_existing`/`total_active_products`/`errors`) so a reviewer can
  see exactly what a monthly refresh did without querying the database
  directly. Reuses `uploads.py`'s own `_save_temp_file` rather than
  duplicating the size-limit/extension-check logic.
- Tests: `backend/tests/test_catalog_merge.py` (service-level, real
  `.xlsx` fixtures via openpyxl, same convention as `test_ingestion.py`) -
  covers in-place update, new-code insertion and its reachability under the
  target upload_id, untouched-when-absent, soft-delete reactivation,
  `product_count` recomputation, index invalidation, no orphaned staging
  rows left behind, and - the property this whole feature exists for - a
  `Match` row confirmed against April's row still resolves correctly after
  May is merged in. Plus two HTTP-level tests appended to
  `test_catalog_api.py` (404 on an unknown version, and a full multipart
  round-trip proving the response shape matches `CatalogMergeResult`).

**No frontend/UI wiring yet** - this shipped as a backend endpoint only,
since the user's ask was specifically about the overwrite mechanics, not a
"Catalog tab has a refresh button" UI. Worth adding a button in
`CatalogTab.tsx` once the endpoint itself is confirmed working, rather
than building both blind in the same pass.

### 15.3 NOT verified - no Docker access in this sandboxed session

Same constraint as every other pass in this document: `pytest` has not
actually been run against this code. Whoever picks this up next should
run

```
docker compose exec backend python -m scripts.migrate_add_master_product_updated_at
docker compose exec backend pytest -q tests/test_catalog_merge.py tests/test_catalog_api.py
```

then a real end-to-end check once a genuine "May" file exists: upload it
via `POST /api/catalog/versions/{id}/update-from-file` against the real
April `CatalogVersion`'s id, and confirm the returned counts look sane
(most rows `updated`, a small number `inserted`, and `unmatched_existing`
should be small too if May really is "mostly the same catalog" - a large
`unmatched_existing` count would suggest the external_id key isn't lining
up the way it did in this pass's check against the April file, and is
worth investigating before trusting the merge).

### 15.4 UI added same pass: "Refresh from file..." button in the Catalog tab

User asked for this once the backend endpoint existed. `CatalogTab.tsx`
gained a hidden `<input type="file">` triggered by a visible "Refresh from
file..." button next to "Show deleted", disabled whenever there's no active
`catalogVersionId` to merge into (tooltip explains why). On file select,
calls the new `updateCatalogFromFile()` (`lib/api.ts`, reusing the existing
`uploadFile()` multipart helper already used by the master/destination
upload buttons elsewhere), shows a dismissible blue summary banner
(`N updated (M reactivated), N inserted, N left untouched`, plus a row-error
count if any), and reloads the table so changed/new rows are visible
immediately. Mirrors the existing delete/undo banner's styling and the
"invalidate-index-then-reload" pattern already used elsewhere in this file.

**NOT verified in a browser** - same constraint as every frontend change in
this document (no browser/Docker access this session). Manual check needed:
open the Catalog tab, click "Refresh from file...", pick a modified copy of
the current catalog, and confirm the banner's counts match what the backend
actually did (cross-check against `test_catalog_merge.py`'s expectations).

### 15.5 Real verification, same day: migration + pytest actually run

User ran the new migration and `pytest -q tests/test_catalog_merge.py
tests/test_catalog_api.py` for real: 32 passed, 2 failed - both pre-existing,
unrelated to this feature (`test_list_products_search_by_name`,
`test_list_products_search_is_case_insensitive`, from section 13's original
work, apparently never actually run in Docker until now). Root-caused by
directly testing SQLite's `lower()`: it does not case-fold Cyrillic
(`lower('Стул')` returns `'Стул'` unchanged, ASCII-only), so
`.ilike()`-based Cyrillic search silently fails to match on the in-memory
SQLite test database - PostgreSQL's own `lower()`/`ILIKE` are locale-aware
and handle this correctly under a UTF-8 locale (the real deployment), so
this is a test-database-only artifact, not a bug in `app/api/catalog.py`.
Fixed in `test_catalog_api.py`'s `client` fixture: an `event.listens_for(engine,
"connect")` hook registers a Python-`str.lower()`-backed SQLite `LOWER()`
override on that one test connection, so the SQLite test DB now case-folds
Cyrillic the way Postgres already does - test-only, zero production code
touched. Not yet re-confirmed with a second pytest run.

---

## 16. Tenth pass (30 July 2026) - LaBSE embeddings enabled (NEXT_STEPS item 4)

User picked this as the next priority: the single biggest documented
accuracy gap (TF-IDF cannot bridge Kazakh<->Russian at all - see section
12.2's cross-check for a concrete, repeatable failure mode from exactly
this gap).

Flipped both sides of the switch the code was already waiting for (see the
comments that were already in place in `backend/Dockerfile` and
`backend/.env` from when this was deliberately left disabled): uncommented
the LaBSE install in the Dockerfile, set `EMBEDDING_PROVIDER=sentence-transformers`
in `.env`. `build_embedding_provider()` (`app/services/search/embeddings.py`)
already fails open to TF-IDF with a loud warning if the model can't load, so
this cannot break startup even if something goes wrong on the first build.

**Real problem caught before it shipped**: a plain `pip install -r
requirements-embeddings.txt` resolves torch's DEFAULT PyPI wheel, which
bundles the full NVIDIA CUDA toolkit as separate dependencies - confirmed
via an actual `pip install --dry-run` in this session, which pulled in a
dozen-plus `nvidia-cu13-*` packages plus `triton`, well beyond the "~2.5 GB"
the Dockerfile's own comment quoted. This container has no GPU to use any
of that for; it would have been pure wasted download/build time on every
fresh build. Fixed by installing the CPU-only torch build first (`pip
install torch --index-url https://download.pytorch.org/whl/cpu`), which
satisfies sentence-transformers' `torch>=1.11.0` requirement so the
following `pip install -r requirements-embeddings.txt` never reaches for
the CUDA version. **Not verified against the real `download.pytorch.org`
index** - this sandbox's network allowlist blocks that domain, so the fix
is based on PyTorch's own well-documented official CPU-wheel install
method, not a confirmed dry-run here. Watch the first few lines of the
real build log to confirm it resolves a `+cpu` tagged wheel, not a plain
one.

**New reusable tool**: `backend/scripts/calibrate_embedding_provider.py` -
the actual HANDOFF.md section 6 benchmark (ground-truth top-1/top-3
accuracy, true-vs-nonmatch score separation, the four named hard cases),
made runnable against the REAL app (real DB, real Qdrant, real configured
embedding provider) instead of the manual process section 6 describes, or
`analysis/independent_matching_check.py`'s deliberately-simplified
standalone reimplementation (which cannot exercise the real embedding
provider at all - that was the whole reason it was built standalone).

Two real risks were caught and designed around before this was safe to
hand off, not left as caveats:

- **Never commits.** Ingests the two real files into throwaway Upload/
  MasterProduct/DestinationProduct rows (same one-shot pattern
  `standalone_matching.run_matching_job` already uses), but `db.rollback()`
  always runs in `finally` - nothing this script ingests is ever left in
  Postgres, so running it repeatedly cannot reproduce section 14.0's
  duplicate-catalog-version bloat.
- **Never touches the real Qdrant collection.** `CatalogSearchIndex.build()`
  always targets whatever `settings.qdrant_collection_name` names, and
  `VectorIndex.build()` (`vector_search.py`) DELETES and recreates that
  collection from scratch on every call - building an index straight
  against the real "products" collection here would have wiped the live
  app's actual search index as a side effect of running a benchmark. The
  script points `settings.qdrant_collection_name` at an isolated
  `calibration_tmp` collection first (safe: this runs as its own separate
  `docker compose exec` process, never the same process as the live
  uvicorn server, so mutating the settings singleton here has zero effect
  on the running app), restores the real name in `finally`, and deletes
  the temporary collection afterward (best-effort - a cleanup failure is
  logged, never allowed to mask the actual benchmark result).

Copied the two real files into `backend/calibration_data/` (a new folder)
so the script can reach them at all: `docker-compose.yml` only bind-mounts
`./backend`, not the project root where the real files actually live.
Originals at the project root are untouched.

**NOT verified - no Docker access in this sandboxed session** (same
constraint as every pass in this document). Whoever runs this next:

```
docker compose up --build   # required, not a restart - Dockerfile changed
docker compose exec backend python -m scripts.calibrate_embedding_provider
```

First build will take a while (large model download, even with the CPU-only
torch fix). Compare the printed top-1/top-3 accuracy and score-separation
numbers against the tfidf-era baseline the script prints alongside them
(97.1%/100%; true mean 0.978/p10 0.949 vs. sample mean 0.449/p90 0.718) - a
narrower gap would mean this change made things worse even if top-1 went
up, per section 6's own rule. Also worth a quick sanity check that
`docker compose logs backend` doesn't show the "Could not load
sentence-transformers model" fallback warning - if it does, LaBSE failed to
load (check network access to huggingface.co) and everything silently ran
on TF-IDF instead, same as before.

Same session, real verification: user confirmed the two pre-existing
Cyrillic-search test failures (section 15.5) are fixed - `.env`'s own
duplicate `ENABLE_LLM_RERANKER_FOR_HARD_CASES` key (line 96 said `false`,
line 111 said `true` - the last one wins when python-dotenv parses it, so
`true` was already the real, active value, but a confusing landmine for
whoever reads it next) was consolidated to one line, one source of truth,
while looking into LLM auto-match verification (NEXT_STEPS.md items 2-3).

---

## 17. Eleventh pass (30 July 2026) - wizard "no resume" fix, both halves

User picked this from the punch list (NEXT_STEPS.md item 6 by way of
HANDOFF section 8): reviewing ~200-1000 items in the Quick Match Wizard in
one uninterrupted sitting is unrealistic, and until now every decision
lived only in the browser tab's JavaScript memory - closing the tab, a
crash, or handing the review off to someone else lost everything.

Scoped in two parts after clarifying with the user what "save point" and
"can someone continue this later" actually required (see the conversation
that led here) - a real fork, since Part B touches classification and
export semantics, not just where a decision gets written:

- **Part A - never lose a decision once made.** Every wizard click
  (confirm a candidate, or "Не подходит") now persists to the database the
  instant it's made, not just at the final "Save & Export" click.
- **Part B - reopen and continue across sessions/devices/people.** A
  "continue a previous run" picker lets someone reopen an existing
  destination upload instead of re-uploading the same file, and
  already-decided items are never re-asked or silently reclassified.

The user specifically confirmed Part A alone would NOT solve their actual
need (re-uploading the same file in a new session creates entirely new
DestinationProduct rows with no memory of prior decisions - it's a second,
parallel pass, not a continuation), so both were built together.

### 17.1 Part A - `POST /api/v1/matching/decisions`

New, lightweight endpoint alongside the existing `/save`: same body shape
(`{rows, catalog_version_id}`), calls the same `save_results()` - but never
builds or returns an export workbook, since this fires on every single
wizard click, not once at the end. `app.js`'s `saveChoice()` now calls
`persistDecision()` immediately (fire-and-forget from the reviewer's
perspective - a slow or failed network call must never block moving to the
next item, same "fail open" shape as every optional-provider fallback in
this project: LLM confirmer, embedding provider, etc.).

**The real risk this had to avoid: writing the same decision twice.** By
the time a reviewer reaches the final "Save & Export" screen, every
decision they made was already persisted via `/decisions`. If `/save` blindly
re-wrote all of them again (as `save_results` always has, matching the
existing `confirm_match`/`reject_match` precedent in `app/api/matching.py`
which also doesn't dedupe), every session would double its own Match/
Feedback rows. Fixed with a new `already_persisted` flag on each row:
`save_results()` skips a row entirely when it's set. `buildSaveRows()`
(`app.js`) sets it for anything already in `persistedDecisions` (populated
by successful `/decisions` calls this session) or anything resumed from a
genuinely earlier session (`record.already_decided` - see Part B). A
decision whose incremental save failed or never completed simply isn't
flagged, so `/save` still writes it at the end - the same fail-open
guarantee as the fire-and-forget call itself, just one layer up.

One more race handled explicitly: `saveResults()` now `await
Promise.allSettled(pendingDecisionSaves)` before building the final rows -
without this, a decision made a split second before clicking "Сохранить"
could still be in flight when `buildSaveRows()` checks `persistedDecisions`,
and both the in-flight call and the final save could end up writing it.

Audit-override persistence (the existing "Отклонить (нет совпадения)"
button in the "авто" detail view, sections 10.3/10.4/11.4) is deliberately
**left unchanged** - still only written at final save time. Not a gap,
a scope decision: that's a quick spot-check action on the summary screens,
not the "1000 items in one sitting" fatigue problem this pass targets.

### 17.2 Part B - `destination_upload_id` on `run/start`, `GET /destinations/resumable`

`run_matching_job()` now accepts `destination_upload_id` as an alternative
to `destination_path`/`destination_filename` - when given, it reuses the
existing DestinationProduct rows for that upload instead of calling
`ingest_destination()` again (mirrors exactly how `catalog_version_id`
already lets a run reuse an existing catalog instead of re-ingesting a
master file - section 4). `GET /api/v1/matching/destinations/resumable`
(`standalone_matching.list_resumable_destinations`) feeds a new picker in
the wizard's input screen ("Продолжить предыдущий подбор"), listing recent
destination uploads with their pending/decided counts - mirrors the
existing "use existing catalog" picker's UI pattern in both HTML and JS
(`setResumeDestination`/`loadResumableDestinations` are near-identical
twins of `setCatalogVersion`/`loadCatalogVersions`).

**The correctness-critical piece**: `build_job_result` now checks each
DestinationProduct's `status` before classifying it. `status != "pending"`
means a real decision already exists (either from Part A's incremental save
earlier in THIS run, or a genuinely earlier session) - `_resume_record_for_decided`
reports that decision directly (from the row's most recent `Match`) instead
of reclassifying. This matters for a real reason, not just efficiency: a
fresh reclassification could legitimately disagree with what a human
already confirmed (an index rebuild, a recalibrated threshold, a different
embedding provider - section 16's LaBSE switch changes every score). A
previously-confirmed choice must never silently change or get re-asked -
same "destructive/consequential things must be deliberate" principle as
`MasterProduct.is_active` elsewhere in this project.

Already-decided rows are bucketed into `auto_matched` (has a real
`master_product_id`) or `no_match` (doesn't) purely so they're excluded
from `needs_review`/the review queue - tagged `already_decided: true` +
`resumed_decision` (`"вручную"`/`"авто"`/`"без совпадения"`, derived from
the stored `Match.match_type`, not guessed) so the frontend can render a
distinct `[продолжено]` badge in the auto/no-match detail views and mark
the row `already_persisted` in `buildSaveRows()`.

**Known v1 limitation, not fixed here**: a resumed item can only be
overridden via the existing "Отклонить" button in the "авто" detail view
(which already works generically on anything in that bucket, resumed or
not) - there's no way to send an already-decided item back into the
interactive wizard flow to pick a *different* candidate. Revisiting a
resumed manual pick requires that same override-to-no-match path today,
not a first-class "re-decide" action.

### 17.3 Tests

`tests/test_standalone_matching.py` gained: `save_results` respects
(`test_save_results_skips_rows_already_persisted`) and requires opt-in for
(`test_save_results_still_persists_rows_not_flagged_already_persisted`) the
new flag; `_resume_record_for_decided` for both a manually-matched and a
rejected row; `build_job_result` routing a decided row through the resume
path while a genuinely pending row still gets classified normally
(deliberately seeded so fresh classification would pick a DIFFERENT
candidate than the stored one, so a bug that ignores `already_decided`
would fail the test, not accidentally pass); `list_resumable_destinations`'
counts and its exclusion of empty uploads; and an end-to-end
`run_matching_job` test (using the existing `shared_engine_session`
fixture pattern) proving a resumed run reuses the same DestinationProduct
row rather than re-ingesting a second copy. New
`tests/test_standalone_matching_api.py` covers the two new endpoints'
HTTP wiring (`/destinations/resumable`, `/decisions`, and the
decisions-then-save no-double-write property) - deliberately does NOT
attempt a TestClient round-trip through the file-upload `run/start`
endpoint, following this project's existing precedent (`test_uploads_api.py`)
of not exercising multipart uploads through a real HTTP test.

### 17.4 NOT verified - no Docker or browser access this session

Same constraint as every pass in this document, and this is the biggest
frontend change yet (`app.js` gained a new fire-and-forget persistence
path, a resume picker, and changed `buildSaveRow`'s signature). Every file
was checked with a real parser before handing this off - `python -m
py_compile`-equivalent (`ast.parse`) on every touched Python file, `node
--check` on `app.js` - so there are no syntax errors, but none of the
actual runtime behavior has been exercised. Before trusting this:

1. `docker compose exec backend pytest -q tests/test_standalone_matching.py tests/test_standalone_matching_api.py`
2. Open the wizard, start a run, make a few decisions, and watch
   `docker compose logs backend -f` for `POST /api/v1/matching/decisions`
   calls firing as you click - confirm they return 200, not silently
   failing (CORS, wrong API base, etc.).
3. Refresh or close the tab mid-review, then check the destination
   upload's rows in the Catalog/database directly (or query
   `/api/matching/{upload_id}/feedback-stats` if wired up) - confirm the
   decisions already made are really there.
4. Start a NEW session, pick that same destination upload from "Продолжить
   предыдущий подбор", and confirm: already-decided items do NOT appear in
   the manual review queue, they show up in the auto/no-match detail views
   tagged `[продолжено]`, and the remaining pending items are exactly the
   ones not yet decided - not fewer, not more.
5. Finish the resumed session and click "Сохранить" - confirm the
   exported Excel has exactly one row per destination product (no
   duplicates from decisions made across two different sessions) and that
   the database has exactly one Match per destination product for each
   decision (`docker compose exec backend python -c` a quick count query,
   or re-run the relevant pytest tests against the real Postgres DB if
   there's an easy way to point them at it).

Real verification, same day: user confirmed all 33 new tests passed in
Docker (`pytest -q tests/test_standalone_matching.py
tests/test_standalone_matching_api.py`). Browser click-through (steps 2-5
above) still not done as of this writing.

---

## 18. Twelfth pass (30 July 2026) - real destination-upload duplicate
cleanup, and Catalog tab "add a new product" (NEXT_STEPS item 7)

### 18.1 Real bloat found via the resume picker itself

Section 17's new "Продолжить предыдущий подбор" picker immediately proved
its worth as a diagnostic: the user's real dropdown showed ~13 identical,
completely untouched `Детсад.xlsx` uploads (repeated testing that
re-uploaded the same file instead of reusing/resuming it - the exact
destination-side mirror of section 14.0's master-catalog duplicate-
CatalogVersion bloat) plus one genuine anomaly: `Казниса апрель.xlsx` (the
*master* catalog file) appearing in the *destination* uploads list -
someone uploaded it as a request list by mistake at some point, the
mirror-image of section 14.0's own "Детсад ingested as a master catalog"
finding.

Confirmed scope with the user before deleting anything (no git, real
review progress existed on some of these - 950/1233, 971/1233, 29/1233
decided): delete only the fully untouched duplicates, keep anything with
real progress and the two non-`Детсад` files regardless of their own
progress state.

New `backend/scripts/cleanup_duplicate_destination_uploads.py` - same
dry-run-by-default pattern as `cleanup_stale_catalog_versions.py`. For each
distinct filename among destination uploads, keeps the single newest copy
unconditionally (mirrors that script's "always keep newest" rule) and
deletes every other same-named copy that has zero decided
DestinationProduct rows. This one rule naturally protects everything the
user asked to protect without any filename-specific special-casing: the
partial-progress `Детсад.xlsx` copies are kept because they have real
decisions on them, and the two singleton files are kept because each is
the "newest" (only) copy of its own filename. Deletes in FK-safe order
(Feedback/MatchCandidate/Match, defensively, before DestinationProduct,
before Upload) even though a zero-decided upload should have none of the
first three. **Run by the user against the real database - confirmed
done.**

### 18.2 Catalog tab: `POST /api/catalog/products` (NEXT_STEPS item 7)

The one gap left in `app/api/catalog.py`: no way to add a single new
catalog row by hand without a full Excel re-upload. New endpoint (201,
`MasterProductCreate` schema - only `product_name` required) attaches the
new row to whichever CatalogVersion is currently active (400s if there
isn't one - unlike edit/delete, a brand-new row has no existing upload_id
to fall back to), derives `normalized_name` the same way ingestion does,
assigns `source_row` as one past the current max for that upload (so it
sorts to the end of the default view instead of interleaving at row 0),
recomputes `product_count` by a real count query (same "recompute, don't
increment" convention as `catalog_merge.py`), and invalidates the cached
search index exactly like every other mutating endpoint here. `raw_data`
carries a `{"_manually_added": true}` marker instead of a real ingested
Excel row, for the same traceability reason every other row's `raw_data`
preserves its original source.

Frontend: `CatalogTab.tsx` gained a blue "+ Add product" button next to
"Refresh from file..." (disabled under the same "no active catalog"
condition), opening an inline new-row form at the top of the table -
same input layout as the existing edit-row form, bound to its own
`newDraft` state so creating and editing an existing row can never be
confused with each other. Saves via the new `createCatalogProduct()`
(`lib/api.ts`), then reloads the current page rather than prepending the
returned row locally - the new row's `source_row` puts it at the END of
the default ordering, so a local prepend would show it in the wrong place
until the next reload anyway.

Tests: five new cases in `test_catalog_api.py` - a full create round-trip
(row attached to the right upload, `normalized_name` derived, list
total/`product_count` both grow), the 400 when no CatalogVersion is
active, `source_row` placing the new row at the end of the default list,
and index invalidation. One test documents rather than fixes a real
observation: `MasterProductCreate.product_name: str` accepts an empty
string today (Pydantic doesn't reject it, and the endpoint doesn't add its
own check) - worth tightening to `constr(min_length=1)` if an empty-name
row ever actually shows up in practice.

**NOT verified - no Docker/browser access this session** (same constraint
as every pass). Also worth noting for whoever picks this up: this session
hit the sandbox's bash-mount truncation bug (HANDOFF section 2) three
separate times while editing `catalog.py`, `CatalogTab.tsx`, and
`test_catalog_api.py` - each time confirmed as the known artifact (not a
real syntax error) by re-reading the full file through the `Read` tool,
never "fixed" via bash. Run before trusting this:

```
docker compose exec backend pytest -q tests/test_catalog_api.py
```

then open the Catalog tab, click "+ Add product", fill in just a name,
save, and confirm the new row appears at the end of the list with the
right `upload_id`/catalog version.
