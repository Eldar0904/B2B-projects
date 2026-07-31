# Architecture — AI Product Matching System

## Status

Phase 1 (Data Pipeline) only. Search, matching UI, feedback loop, and ML phases
are not yet implemented — see `B2B.md` for the full spec and `README.md` for
the phase plan.

## Repository state at start of Phase 1

Empty project folder plus two real input files used for development/testing:

- `Казниса апрель.xlsx` — master catalog (sheet `База КазНИИСА 04.2026`,
  ~5,195 rows, hierarchical price-list format with group header rows).
- `Детсад.xlsx` — destination file (sheet `Список сводный д.сад`, ~2,885
  rows).

## Tech stack (Phase 1)

- **Backend**: Python 3.11+, FastAPI
- **ORM**: SQLAlchemy 2.x
- **Database**: PostgreSQL (via `docker-compose.yml`). A `sqlite` fallback
  URL is supported for zero-dependency local testing (set `DATABASE_URL` to
  a `sqlite:///` path) — this is what was used to validate ingestion against
  the real files in this environment, since no Postgres server was available
  here. Production/dev-with-docker should use the Postgres URL.
- **Excel parsing**: `openpyxl` (via pandas) — read-only, streaming rows to
  avoid loading entire huge sheets into memory as Python objects at once.
- **Frontend**: not built in Phase 1. Per spec, Next.js/React/TypeScript
  comes in Phase 3 (Matching UI).

Qdrant, Redis, and the job queue are Phase 2+ concerns and are intentionally
absent from Phase 1.

## Module layout

```
backend/
  app/
    config.py          settings (DATABASE_URL, upload limits) via pydantic-settings
    database.py         SQLAlchemy engine/session, Base
    models.py            Upload, MasterProduct, DestinationProduct ORM models
    schemas.py            Pydantic response models
    services/
      excel_reader.py      sheet listing, header-row detection, row streaming
      column_mapper.py     alias dictionary -> canonical field mapping, manual override
      normalizer.py         text normalization pipeline
      ingestion.py          orchestrates read -> map -> normalize -> persist, per-row
                            error isolation, ingestion report
    api/
      uploads.py            POST /api/uploads/master, /destination, status, sheets
    main.py                 FastAPI app, router registration
  tests/                    pytest unit tests for each service
  requirements.txt
scripts/
  ingest_master.py          CLI to ingest a master catalog file end-to-end
  ingest_destination.py     CLI to ingest a destination file end-to-end
docker-compose.yml          postgres:16 service
.env.example
```

## Data flow (Phase 1 scope)

```
Excel file
   -> excel_reader: list sheets, detect header row, stream rows as dicts
   -> column_mapper: map raw headers (Наименование/Название/Product Name/...)
      to canonical fields (product_name, description, price, unit, code, ...)
      falls back to a per-upload manual mapping override if auto-detection
      is incomplete
   -> normalizer: lowercase, trim, collapse whitespace, normalize punctuation/
      quotes/hyphens, normalize ё/е
   -> ingestion: persist one row at a time; malformed rows are logged and
      skipped, never abort the whole batch; raw row is preserved as JSON in
      raw_data so no original data is lost
   -> PostgreSQL: `uploads`, `master_products` / `destination_products` rows
```

## Key design decisions specific to these two files

- The master file's `База КазНИИСА` sheet mixes three row kinds: section
  headers (only column A filled, e.g. "Отдел 52. Технологическое..."),
  group headers (a code + name but no unit/price, e.g. `521-101-0100`
  "Оборудования игровое"), and real priced products (code + name + unit +
  price, e.g. `521-101-0131-0001`). Phase 1 stores all three as rows with a
  computed `is_group_header` flag (true when both unit and price are empty)
  rather than discarding non-product rows, so no source data is lost and
  later phases can decide how to use the category hierarchy.
- The destination file already contains a precomputed `поисковый текст`
  (search text) and `поисковая маска` (search mask) column — Phase 1 maps
  these through as extra normalized/searchable text but generates its own
  `normalized_name` independently rather than trusting the source file's
  precomputed text, since the normalization rules must be consistent across
  master and destination.
- Column mapping is alias-dictionary based (Russian + English variants),
  not ML-based, per spec section 5. This keeps Phase 1 deterministic and
  fast on tens of thousands of rows.

## What's deliberately NOT in Phase 1

- No embeddings, no Qdrant, no vector search (Phase 2).
- No matching UI (Phase 3).
- No feedback tables populated with real decisions (schema exists per spec
  section 31 for forward compatibility, but nothing writes to it yet).
- No supervised ML / reranking.

## Phase 2 — Search

Scope per spec section 36: keyword search + fuzzy matching + embeddings +
Qdrant, tested as "destination product -> top 20 candidates."

### Retrieval methods

- **Keyword (BM25)**: `rank_bm25.BM25Okapi` over each master product's
  `normalized_name` + `description`, tokenized on whitespace after the
  Phase 1 normalization pipeline. Rebuilt in-process from the DB (no
  separate persistence needed — ~tens of thousands of rows fit comfortably
  in memory and rebuilding is fast; this can move to a proper search
  engine like Postgres `tsvector`/Elasticsearch later if the catalog grows
  much larger).
- **Fuzzy**: `rapidfuzz.process.extract` (token-sort-ratio) over the same
  normalized names. Catches near-duplicates and typos that BM25's
  token-overlap model misses (e.g. "Столь" vs "Стол").
- **Vector**: cosine similarity over sentence embeddings, retrieved from a
  single Qdrant collection named `products` (per spec section 9 — one
  collection, metadata-filtered, not one DB per category).

### Embedding model — two providers, one interface

`app/services/search/embeddings.py` defines an `EmbeddingProvider`
interface with two implementations, selected via `EMBEDDING_PROVIDER` in
config:

- `SentenceTransformerEmbeddingProvider` — the real answer per spec
  section 12: a multilingual sentence-transformers model (default:
  `paraphrase-multilingual-mpnet-base-v2`) so Russian/Kazakh/English
  product text embeds into one comparable space. This is what should run
  in the user's actual deployment.
- `TfidfEmbeddingProvider` — a scikit-learn character n-gram TF-IDF
  fallback used as the **default in this dev/test environment**, because
  this sandbox has no outbound access to huggingface.co to download model
  weights (verified: `curl -I https://huggingface.co` returns
  `403 blocked-by-allowlist`). It requires no network access and no GPU,
  and is enough to prove the retrieval/scoring pipeline end-to-end.
  Character n-grams (not word n-grams) so it still captures partial/typo
  similarity for Cyrillic text without a real semantic model.

  **Dimensionality trade-off found during real-data validation:** the
  TF-IDF vectorizer's `max_features` directly sets embedding dimension,
  and Qdrant's HNSW index build time scales with both point count and
  dimension. At the initial default (20,000 features) indexing the real
  ~5,000-row master catalog in embedded local mode did not finish in a
  reasonable window; at 512 features it took ~50s; at 256 features (the
  current default) it took ~31s. 256 is kept as the default for the
  offline fallback since this environment has no server-side Qdrant to
  offload the HNSW build to. This constraint is specific to the TF-IDF
  fallback in embedded/local mode — a real Qdrant server handles much
  higher-dimensional vectors (e.g. 768 from sentence-transformers)
  comfortably, since it's a dedicated, persistent, optimized process
  rather than an embedded engine sharing the Python process's resources.

Switching to the real multilingual model on a machine with normal
internet access is a one-line config change
(`EMBEDDING_PROVIDER=sentence-transformers`); no code changes needed. The
rest of the pipeline (Qdrant storage, hybrid combination, scoring) is
identical either way since both providers implement the same
`embed(texts: list[str]) -> list[list[float]]` interface.

### Qdrant: local (embedded) vs server mode

`vector_search.py` builds its `QdrantClient` from config:

- If `QDRANT_URL` is set, connects to a real Qdrant server (the
  `docker-compose.yml` service, for normal local/dev use with Docker).
- Otherwise, falls back to Qdrant's embedded local mode
  (`QdrantClient(path=QDRANT_LOCAL_PATH)`), an on-disk engine with no
  server process required. This is what was used to validate Phase 2 in
  this sandbox (no Docker available here) and is also handy for anyone
  running the backend without wanting to stand up Postgres+Qdrant
  containers just to try it out.

Both modes use the identical client API, so no code branches deeper than
client construction.

### Combining candidates and scoring

`hybrid_search.py` retrieves up to 20 candidates from each method, dedupes
by `master_product_id`, and computes `final_score` using the exact
weighted formula from spec section 14:

```
final_score =
    0.35 * embedding_score
  + 0.25 * keyword_score
  + 0.15 * fuzzy_name_score
  + 0.10 * category_score
  + 0.10 * attribute_score
  + 0.05 * identifier_score
```

`category_score`, `attribute_score`, and `identifier_score` are `0.0` for
every candidate in Phase 2 — category classification, attribute
extraction, and identifier matching aren't built yet (they depend on work
scheduled for later phases / section 7-8 of the spec). This means the
Phase 2 `final_score` currently tops out around `0.75` for a perfect
embedding+keyword+fuzzy match; that ceiling will rise as those signals are
added. All six weights live in `app/services/search/scoring.py` as plain
config, per spec's "do not assume these weights are optimal" instruction.

Rows flagged `is_group_header=true` (from Phase 1) are excluded from
candidates — they're catalog section headers, not purchasable products.

### What's deliberately NOT in Phase 2

- No reranking (cross-encoder or LLM) — that's Phase 6. Phase 2's output
  is the top-20 candidate pool with scores, not the top-3 shown to a user.
- No category/attribute-based filtering before retrieval (spec sections 7
  and 9's "filter master catalog by category" isn't implemented yet since
  category classification hasn't been built) — Phase 2 runs all three
  retrieval methods over the whole non-group-header catalog.
- No confidence thresholds / auto-accept logic — that's Phase 5.

## Phase 3 — Matching UI

Scope per spec section 36: destination product -> top 3 candidates -> user
selection, "usable before adding advanced ML."

### Workflow is keyed by destination upload, not a separate "job" table

The spec's API sketch (section 32) talks about a `job_id`. Rather than
introduce a new `matching_jobs` table purely to hold an id, Phase 3 uses
the destination upload's own `id` (from Phase 1's `uploads` table) as that
identifier — a "matching job" *is* "review the destination products from
this upload." This avoids a redundant table while keeping the same
externally-visible shape (`/api/matching/{upload_id}/...`).

### Endpoints (`app/api/matching.py`)

- `POST /api/matching/{upload_id}/start` — verifies the search index
  (Phase 2) is built; returns initial progress. Doesn't create any new
  state — matching only reads `destination_products.status`.
- `GET /api/matching/{upload_id}/progress` — counts by status
  (`pending`, `matched`, `no_match`) for that upload, per spec section 30.
- `GET /api/matching/{upload_id}/next` — the next `pending` destination
  product plus its top-3 hybrid search candidates (Phase 2's
  `retrieve_candidates`, `top_k=3`) with a per-candidate explanation.
- `POST /api/matching/{upload_id}/confirm` — body `{destination_product_id,
  master_product_id, rank, candidates}`. Writes a `Match` row
  (`is_confirmed=True`, `method="user_selected"`) and the full candidate
  set as JSON on that row for traceability, then sets
  `destination_product.status = "matched"`.
- `POST /api/matching/{upload_id}/reject` — body `{destination_product_id}`.
  Sets `status = "no_match"`. Per spec section 20, formal `feedback` table
  population (with `decision_type` etc.) is Phase 4's job; Phase 3 only
  needs the status transition to make "move to next product" work.
- `POST /api/matching/{upload_id}/manual-search` — body `{query}`. Reuses
  the Phase 2 index directly (not scoped to top-3) so a user can search
  the whole master catalog when none of the 3 shown candidates fit (spec
  section 19).

### Explanations are derived from real scores, never fake (spec section 18)

`build_explanation()` in `app/services/matching.py` looks at each
candidate's actual `keyword_score`, `fuzzy_name_score`, `embedding_score`,
and `matched_by` set and turns them into short factual statements, e.g.
"Exact keyword overlap", "89% fuzzy name similarity", "Found by 3 of 3
retrieval methods." No category/attribute claims are made since Phase 1/2
don't extract those yet — the explanation only ever states what the
system actually computed.

### Frontend (`frontend/`)

Next.js + React + TypeScript + Tailwind, per spec section 3, implementing
the review screen mockup in spec section 17: progress bar, destination
product panel, three candidate cards with similarity percentages and an
expandable "why this match" panel, Confirm / None of these / Search
manually actions, and keyboard shortcuts (`1`/`2`/`3` select, `N` none,
`Enter` confirm) per spec section 17. It is a thin client over the
Phase 3 API — no matching logic lives in the frontend.

### What's deliberately NOT in Phase 3

- No automatic acceptance by confidence threshold (Phase 5).
- No cross-encoder/LLM reranking (Phase 6) — the 3 candidates shown are
  Phase 2's raw hybrid-search top-3, unreranked.
- No formal `feedback` table writes (Phase 4) — only `matches` and
  `destination_products.status` are updated.

## Phase 4 — Feedback

Scope per spec section 36/20: save every user decision, build the
feedback database. The `feedback` table already existed in
`backend/app/models.py` as a forward-compatible placeholder since Phase 1
- Phase 4 is the first phase that actually writes to it.

### Every decision writes both a status update and a Feedback row

Phase 3's `confirm`/`reject` endpoints already updated
`destination_product.status` and (for confirms) created a `Match` row.
Phase 4 adds a `Feedback` row alongside those on every decision, matching
the spec's schema in section 20 exactly:

```json
{
  "destination_product": "<name>",
  "selected_master_product": "<name or null>",
  "candidates": [{"id": "...", "rank": 1, "score": 0.83}, ...],
  "decision": "user_selected"
}
```

stored as `candidate_data`, with `selected_master_product_id` and
`decision_type` as separate indexable columns so later phases (7:
training data, 8: active learning) don't have to parse JSON to filter.

### `decision_type` mapping

Per spec section 20's list (`auto_accepted`, `user_selected`,
`user_rejected`, `manual_search_selected`, `no_match`), Phase 4 uses:

- **`user_selected`** — user confirmed one of the top-3 hybrid-search
  candidates (`rank` 1-3).
- **`manual_search_selected`** — user confirmed a result found via
  `POST /api/matching/{upload_id}/manual-search` instead of the top-3
  (the frontend passes `rank=0` as the sentinel for "came from manual
  search," which the service maps to this decision type).
  candidate.
- **`no_match`** — user clicked "None of these"; no master product
  applies.
- **`auto_accepted`** — reserved for Phase 5 (confidence-threshold
  automatic matching isn't built yet, so nothing produces this value
  yet, but the column/value already exists for when it is).
- **`user_rejected`** — reserved for a future finer-grained "reject this
  specific candidate" action; the current 3-button UI (Confirm / None /
  Search manually) doesn't have a way to reject one candidate without
  either picking another or declaring no match, so nothing produces this
  value yet either. Left in the type vocabulary since spec section 20
  lists it explicitly.

### What's deliberately NOT in Phase 4

- No training dataset generation from feedback (Phase 7).
- No active learning / uncertainty-based prioritization (Phase 8).
- No `auto_accepted` feedback rows (Phase 5 doesn't exist yet, so nothing
  is auto-matched).

## Phase 5 — Automatic Matching

Scope per spec section 36/16: `confidence > threshold -> automatic
match`, "only enable after evaluation."

### Two independent sources of automation

1. **Exact match (spec section 11)** — before any hybrid scoring, check
   whether a destination product's `external_id` (code/SKU) or
   `normalized_name` exactly equals a master product's. If so, auto-accept
   at `confidence = 0.99` (configurable via `EXACT_MATCH_CONFIDENCE`),
   method `"exact_match"`. This is deliberately independent of the Phase 2
   hybrid score - it doesn't need category/attribute signals to be
   trustworthy, since an identical code or identical normalized name is
   about as strong a signal as this system can get without human
   confirmation.
2. **Hybrid-score threshold (spec section 16)** — if no exact match,
   fall back to Phase 2's `final_score`. If it's `>= HIGH_CONFIDENCE_THRESHOLD`
   (default `0.95`), auto-accept with method `"auto_threshold"`.

### Why the default threshold will rarely fire, and why that's fine

Phase 2's `final_score` currently maxes out around `0.75` in practice
(`category_score`, `attribute_score`, `identifier_score` are still `0.0`
- see Phase 2 notes above). Keeping the spec's literal default of `0.95`
for `high_confidence_threshold` means the hybrid-score auto-accept path
will rarely or never trigger until later phases add those missing
signals. This is intentional, not an oversight: the alternative would be
lowering the threshold to whatever number happens to make the demo fire,
which is exactly the kind of unvalidated tuning spec section 14 warns
against ("do not assume these weights/thresholds are optimal"). Real
automation today comes from the exact-match path, which needed no such
guessing. The threshold is fully configurable
(`HIGH_CONFIDENCE_THRESHOLD` / `MEDIUM_CONFIDENCE_THRESHOLD` in `.env`)
for whenever real evaluation data justifies changing it.

### Confidence levels surfaced to the UI (spec section 16)

`GET /api/matching/{upload_id}/next` now also returns a
`confidence_level` (`"high" | "medium" | "low"`) computed from the top
candidate's `final_score` against the two thresholds, and
`no_reliable_match: true` when the level is `"low"` - matching spec
section 16's "still display the top 3 candidates" behavior even when
confidence is low, rather than hiding them.

### Batch auto-match endpoint

`POST /api/matching/{upload_id}/auto-match` runs the auto-match check
over every `pending` destination product in that upload: whatever
qualifies (exact match or, rarely, threshold match) gets a `Match` +
`Feedback` row (`decision_type = "auto_accepted"`) and moves to
`status = "matched"` without human review; everything else stays
`pending` for the normal Phase 3 review flow. This is an explicit,
separate action (not automatic on ingest/reindex) so a human always
chooses when to enable automation for a given batch, per spec's "only
enable after evaluation."

### Low-confidence auto-reject (added after initial delivery, symmetric to the above)

A user pointed out that the automation was asymmetric: high-confidence
items auto-accept, but everything else - including items with
essentially zero signal, where the top "candidate" is obviously not a
match - still required a human to explicitly click "None of these" one
at a time. `LOW_CONFIDENCE_THRESHOLD` (default `0.15`) and
`ENABLE_LOW_CONFIDENCE_AUTO_REJECT` (default `false`, opt-in) close that
gap: if neither the exact-match nor threshold-accept path qualifies, and
the top candidate's `final_score` is below `LOW_CONFIDENCE_THRESHOLD`
(or there are no candidates at all), the item is automatically marked
`no_match` instead of staying pending.

This uses a distinct `decision_type` - **`auto_rejected`** - rather than
reusing plain `no_match`, so `feedback-stats` can tell an automatic
rejection apart from a human deliberately clicking "None of these."
This is an extension beyond spec section 20's literal five decision
types, added because the spec's own automation philosophy (section 24:
"reduce human workload," symmetric high/low confidence examples) implies
this is in the spirit of the design even though section 20 didn't name
it explicitly. `run_auto_match_batch` and `try_auto_match` both accept an
optional `low_threshold` parameter; when `None` (the default), behavior
is identical to before this feature existed - nothing is auto-rejected,
everything non-qualifying stays pending.

### What's deliberately NOT in Phase 5

- No automatic threshold tuning/evaluation loop (would need labeled data
  - that's closer to Phase 7).
- No partial-credit blending of exact-match and hybrid scores; a product
  either qualifies for exact match, qualifies for threshold auto-accept,
  qualifies for low-confidence auto-reject, or goes to human review - no
  fifth "almost automatic" state.

## Phase 6 — Reranking

Scope per spec section 15/36: after Phase 2 retrieves the top-20 pool,
rerank before truncating to the top-3 shown to a human. Spec offers two
options (cross-encoder, or LLM for hard cases) and says "do NOT call an
expensive LLM for every product if a cheaper model can confidently
identify the match."

### Three reranker implementations, one interface

`app/services/search/reranking.py` defines a `Reranker` interface
(`rerank(query_text, candidates) -> list[ScoredCandidate]`, populating
each candidate's `reranker_score` and returning them re-sorted), with:

- **`RRFReranker`** (default, offline-safe) - Reciprocal Rank Fusion: for
  each candidate, rank it within the pool separately by `keyword_score`,
  `fuzzy_name_score`, and `embedding_score`, then combine as
  `sum(1 / (k + rank_i))` (standard RRF, `k=60`). This is a real,
  established reranking technique (used natively by Elasticsearch and
  Qdrant's own fusion query) - not a placeholder. It's a genuinely
  different signal from Phase 2's weighted score-sum: RRF is robust to the
  fact that BM25, fuzzy-ratio, and cosine-similarity scores live on
  different, not-directly-comparable scales, which a weighted sum of raw
  scores can't fully correct for. No model download or network access
  needed, so it's the default here for the same reason `TfidfEmbeddingProvider`
  is Phase 2's default.
- **`CrossEncoderReranker`** (optional, the spec's "Option A") - a real
  multilingual cross-encoder (default model:
  `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`) that scores each
  (destination text, candidate text) pair jointly, which is strictly more
  accurate than comparing independently-computed embeddings. Lazily
  imports `sentence-transformers`; same story as Phase 2's embedding
  provider - needs `requirements-embeddings.txt` and network access to
  huggingface.co, neither available in this sandbox, so it wasn't
  exercised against real data here (see validation notes below).
- **`LLMReranker`** (optional, the spec's "Option B", for hard cases only)
  - calls an LLM (via the `anthropic` SDK, lazily imported) with the
    destination product and its top few candidates, asking it to pick the
    best match or say none apply. Only invoked when `RRFReranker`'s (or
    the cross-encoder's) top-2 candidates are within
    `LLM_AMBIGUITY_THRESHOLD` of each other - i.e., genuinely ambiguous
    cases, per spec's "do not call an expensive LLM for every product."
    Requires `ANTHROPIC_API_KEY`; not called at all if unset. Not
    exercised against a real API in this sandbox (no key available here),
    but the interface, prompt construction, and fallback behavior (falls
    back to the non-LLM ranking if the call fails or isn't configured)
    are implemented and unit-tested with a mocked client.

### Selection and cascade

`RERANKER_PROVIDER` in config picks the base reranker (`"rrf"` default,
or `"cross_encoder"`). `ENABLE_LLM_RERANKER_FOR_HARD_CASES` (default
`false`) layers the LLM tie-breaker on top of whichever base reranker is
active, only for the ambiguous top-2 cases - implementing spec section
15's cascade:

```
Easy (Phase 5 exact/threshold auto-accept)
Medium (hybrid search + base reranker)
Hard (base reranker uncertain -> LLM tie-break, if enabled)
Very hard (LLM unavailable/declines -> human review, unchanged)
```

### Where it plugs in

`get_top_candidates()` in `app/services/matching.py` now retrieves a
wider pool (`RERANK_POOL_SIZE`, default 20, matching spec section 13's
"top 20 candidates") from Phase 2, reranks that pool, then returns the
top 3 by `reranker_score` instead of raw `final_score`. This only changes
*ordering* within the already-retrieved pool - reranking never invents a
candidate that Phase 2's hybrid search didn't already find.

### What's deliberately NOT in Phase 6

- No training/fine-tuning of a custom cross-encoder (would need labeled
  data - Phase 7 territory).
- No LLM reranking exercised against a real API in this environment (no
  API key here); the code path, prompt, and fallback are implemented and
  tested with a mock, but real-data validation only covers `RRFReranker`.
- `reranker_score` is not blended back into `final_score` or into
  auto-match thresholds (Phase 5) - it only determines top-3 ordering for
  human review, keeping Phase 5's auto-accept logic unchanged and easy to
  reason about.

### Caveat found while testing: `LLM_AMBIGUITY_THRESHOLD` is base-reranker-scale-dependent

`RRFReranker` scores live in a very compressed range - with `k=60`, the
score gap between rank 1 and rank 2 candidates on a single method is only
about `1/61 - 1/62 ≈ 0.00026`; summed across three methods, the largest
possible gap between two candidates is still under `0.001`. A
cross-encoder's scores, by contrast, span roughly `0-1` with much larger,
more meaningful gaps. `LLM_AMBIGUITY_THRESHOLD`'s spec-inspired default
(`0.05`) makes sense for a cross-encoder base reranker but would call the
LLM on almost every single decision if used with the RRF base reranker,
since RRF's score gaps rarely exceed `0.001`. This was caught while
writing `test_llm_reranker_only_calls_llm_when_ambiguous` (the "clear
winner, skip the LLM" test needed a threshold around `0.0001`, not
`0.05`, to behave correctly against RRF). If you enable the LLM
tie-breaker with `RERANKER_PROVIDER=rrf`, set `LLM_AMBIGUITY_THRESHOLD`
much lower than the default - this is exactly the kind of
deployment-specific tuning spec section 14 expects, not a bug to route
around with a single universal constant.

### Real-data validation notes

Ran both `search()` (raw final_score order) and `search_reranked()` (RRF)
against real destination products from `Детсад.xlsx`. Two honest
findings:

- For a clean case like "Холодильный шкаф 1 дверь vsp-1", RRF kept the
  already-correct top result ("Шкаф холодильный") in first place -
  reranking didn't break a good result, which is the minimum bar.
- For several low-confidence cases (destination products with no close
  equivalent in this master catalog - the same domain-mismatch cases
  noted in Phase 2), RRF reordered the top-3 in ways that aren't
  obviously "better," e.g. promoting a "Слайсер" (slicer) above a
  chemistry lab kit for a Kazakh-language book query where nothing in the
  catalog is actually relevant. This is a known, real trade-off of RRF:
  it fuses *rank* across methods and discards *how confident* each method
  was, so a candidate that's a razor-thin #1 on one method and mediocre
  elsewhere can outrank one that scored clearly better on the weighted
  sum. This mostly matters where every candidate is already a poor match
  - exactly the situation `no_reliable_match` (Phase 5's confidence
  classification) already flags to the user, so the practical impact on
  a human reviewer is limited, but it's a genuine limitation worth
  knowing rather than glossing over.

## Phase 7 — Supervised Learning

Scope per spec section 21-23/36: once enough confirmed matches exist,
generate a training dataset from feedback, train a model to predict
`P(match)`, evaluate it against the existing baseline, and deploy only if
it actually improves things. Spec is explicit that this comes *after* a
strong baseline (Phases 1-6) and is gated on volume: "500+ verified
matches... Deploy only if performance improves."

### Model backend: scikit-learn by default, XGBoost/LightGBM optional

Spec section 23 names XGBoost/LightGBM specifically. This sandbox's
network egress could not download either (repeated `pip download`
attempts for the `xgboost` wheel returned zero bytes after 30-40s,
despite PyPI's index itself being reachable - this looks like a
size/transfer restriction on this environment's proxy, distinct from the
huggingface.co domain block found in Phase 2). Rather than block Phase 7
on that, `app/services/ml/model.py` defines a `MatchClassifier` interface
with:

- `SklearnGradientBoostingModel` (default) - scikit-learn's
  `GradientBoostingClassifier`, a real gradient-boosted trees model
  (structurally the same family as XGBoost/LightGBM, just scikit-learn's
  reference implementation), already a project dependency, no extra
  install needed.
- `XGBoostModel` / `LightGBMModel` (optional) - lazy-imported, for
  environments where those installs succeed normally (most real
  development machines won't hit whatever this sandbox's transfer
  restriction is). Selected via `ML_MODEL_BACKEND` in config.

### Feature set is honestly smaller than spec section 23's list

Spec section 23 lists: `embedding_similarity, bm25_score,
fuzzy_name_score, category_match, subcategory_match, product_type_match,
brand_match, model_match, dimensions_match, material_match, unit_match,
price_difference`. This project currently computes:

- `embedding_similarity`, `bm25_score`, `fuzzy_name_score` - directly
  from Phase 2's `ScoredCandidate` sub-scores.
- `price_difference` - normalized `abs(dest.price - master.price) /
  max(dest.price, master.price)`, since both sides do store price.

The rest - `category_match`, `subcategory_match`, `product_type_match`,
`brand_match`, `model_match`, `dimensions_match`, `material_match`,
`unit_match` - are **not available** and are not faked as zeros mixed in
silently; they're simply absent from the feature vector, and this is
documented rather than glossed over, for two concrete reasons:

1. Category/subcategory/product-type/brand/model/dimensions/material
   values would come from the attribute extraction pipeline in spec
   section 7, which no phase so far has implemented (Phase 1 only stores
   raw text + a few structured columns; category classification from
   spec section 8 is likewise not built).
2. `unit_match` specifically can't be computed even though `MasterProduct`
   has a `unit` column, because `DestinationProduct` never mapped one -
   Phase 1's destination ingestion only pulls `product_name`,
   `description`, `quantity`, and `price` (see `ingestion.py::_build_record`),
   not `unit`, even though `column_mapper.py` already has a `unit` alias
   dictionary entry. This is a real, fixable gap for a future pass, not
   something Phase 7 should paper over.

Practical consequence: with only 4 features (3 of which are already
inputs to Phase 2's hand-tuned linear formula), a learned model here is
mostly re-deriving better *weights* for those same three retrieval
signals, plus adding price as a new signal - a legitimate, real
improvement over hand-picked weights, but not the qualitative leap
"category/brand/model matching" would represent. That leap needs
attribute extraction and category classification to exist first.

### Training dataset generation (spec section 22)

`app/services/ml/dataset.py::build_training_pairs()` reads every
`Feedback` row and produces labeled pairs:

- **Positive** (`label=1`): `decision_type` in `user_selected`,
  `manual_search_selected`, `auto_accepted`, paired with
  `selected_master_product_id`.
- **Negative** (`label=0`, hard negatives): every *other* candidate in
  that feedback row's stored `candidate_data.candidates` list - i.e.
  products the hybrid search ranked highly enough to show, but the human
  didn't pick. Spec section 22 explicitly calls hard negatives "much more
  useful than obviously unrelated products," and this is exactly what
  they are: near-misses the retrieval system itself considered plausible.
- For `no_match` feedback, every shown candidate becomes a negative (none
  of them were right).

Since `Feedback.candidate_data` only stores `{id, rank, score}` (the
blended `final_score`, from Phase 4), not the three individual
sub-scores, `build_training_pairs()` re-runs the hybrid search index for
each destination product at dataset-build time to recover
`embedding_score` / `keyword_score` / `fuzzy_name_score` for every
referenced candidate id. This requires the search index to be built
first (same requirement as Phase 3/5/6).

### The 500+ example gate is real, not decorative

`GET /api/ml/training-readiness` reports the current count of
"trainable" feedback rows (positive + negative pairs) against
`ML_TRAINING_MIN_EXAMPLES` (default `500`, matching spec section 21) and
`POST /api/ml/train` refuses to train below that threshold, returning
exactly how many more examples are needed rather than silently training
on whatever's available. This project's own accumulated feedback across
all the validation runs in Phases 3-6 is nowhere near 500 - each
validation used a fresh, throwaway SQLite database that was deleted
afterward specifically so it wouldn't leave test artifacts in the real
project files - so the honest state of this repository right now is
"pipeline built and unit-tested, not yet trained on real data," which is
exactly what spec section 21's ordering ("no training data -> hybrid
search -> user verifies -> save feedback -> [after enough examples]
train") describes as the expected state before that volume exists.

### Evaluation gate before deployment (spec section 21/23)

`app/services/ml/evaluate.py::compare_to_baseline()` splits the labeled
pairs into train/test, trains the model on train, and compares its
test-set accuracy/AUC against the "baseline" (Phase 2's existing linear
`final_score` used directly as a threshold-0.5 classifier). Returns both
metrics plus a `should_deploy: bool` that's only `True` if the trained
model's AUC beats the baseline's by more than
`ML_MIN_IMPROVEMENT_MARGIN` (default `0.02`) - spec's "deploy only if
performance improves," made concrete and not just aspirational text.

### What's deliberately NOT in Phase 7

- No actual deployment of a trained model into the live scoring path -
  Phase 7 delivers the training/evaluation pipeline and the gate; wiring
  a model that passed evaluation into `hybrid_search.py`'s scoring is a
  follow-on step once real feedback volume and a passing evaluation both
  exist.
- No category/attribute/unit features (see above) - would need spec
  section 7/8's attribute extraction and category classification first.
- No online/incremental learning - training is an explicit, on-demand
  batch action (`POST /api/ml/train`), matching Phase 5's "explicit,
  opt-in" pattern for anything that changes matching behavior.

## Phase 8 — Active Learning

Scope per spec section 24/36: automatically identify uncertain cases and
prioritize them for human review, reducing human workload by focusing
review effort where the system is least confident.

### Uncertainty = the margin between the top-2 candidates

Spec section 24's own example defines this precisely: `candidate1=0.51,
candidate2=0.49` is "highly uncertain" (margin `0.02`) and should go to a
human first; `candidate1=0.99, candidate2=0.32` (margin `0.67`) can be
auto-accepted (that's Phase 5's job). Phase 8 reuses this exact idea -
the same "ambiguity gap" concept already built for Phase 6's LLM
tie-breaker - but applies it to *ordering the human review queue* instead
of deciding whether to call an LLM.

`matching.compute_uncertainty_margin()` retrieves each pending
destination product's top-2 reranked candidates and returns
`top1.reranker_score - top2.reranker_score`. A small margin means the
system found two (or more) plausible candidates it can't confidently
separate - exactly the case where a human's judgment adds the most value.
A product with zero or one candidate has no margin to speak of
(`None`) - nothing to be "uncertain between."

### Storage: a computed column, not a live recomputation on every request

Computing the margin requires running the full hybrid search + reranking
pipeline for a destination product - too expensive to redo on every
`GET /next` call across a queue of thousands. Phase 8 adds
`destination_products.uncertainty_margin` (nullable `float`, `NULL` until
computed) and a batch endpoint,
`POST /api/matching/{upload_id}/prioritize`, that computes and stores it
for every currently-`pending` product in one pass - the same
explicit-batch-action pattern as Phase 5's `/auto-match` (never runs
silently on ingest/reindex; a human chooses when to spend the compute).

### Two review-queue strategies, same endpoint

`GET /api/matching/{upload_id}/next?strategy=...`:

- **`sequential`** (default, unchanged from Phase 3) - `source_row` order,
  i.e. the order products appeared in the original destination Excel
  file. Kept as the default so nothing about Phases 3-7's behavior or
  tests changes unless a caller explicitly opts in to Phase 8.
- **`uncertainty`** - orders by `uncertainty_margin` ascending (smallest
  margin - most ambiguous - first), falling back to `source_row` order
  for any product that hasn't been prioritized yet (`uncertainty_margin
  IS NULL`), so calling `/next?strategy=uncertainty` before ever running
  `/prioritize` degrades gracefully to sequential order rather than
  erroring.

The frontend's "Prioritize by uncertainty" toggle calls `/prioritize`
once, then switches its `/next` calls to `strategy=uncertainty` for the
rest of the review session.

### Caveat found while testing: RRF margins can tie regardless of true ambiguity

The default reranker (`RRFReranker`, see Phase 6) scores by rank
position, not score magnitude. This means that with a very small master
catalog, "2nd place" produces a numerically identical margin whether the
2nd-place candidate is a close near-duplicate or something totally
unrelated - RRF has no way to tell those apart if both simply land at
rank 2 across all three retrieval methods. This surfaced directly while
writing `test_ambiguous_product_has_smaller_margin_than_unambiguous_one`:
with only 4 master products, an intentionally ambiguous query (two very
similar tables) and an intentionally unambiguous one (one exact chair
match, one clearly unrelated item) produced the *exact same*
`uncertainty_margin` (`0.000793...`) both times, because in a tiny
catalog "rank 2" is rank 2 regardless of relevance. Adding more
unrelated filler products (so the unambiguous query's "2nd place"
becomes *inconsistent* across keyword/fuzzy/vector - 2nd on one method,
lower on the others - rather than the ambiguous query's *consistent*
2nd place across all three) restored the expected direction. In
practice, on a real catalog of thousands of products this effect is far
less likely to matter (true near-duplicates consistently rank close
together; true non-matches rarely tie for 2nd across all three
methods), but it's worth knowing that `uncertainty_margin` measures
*rank-agreement uncertainty*, not confidence-magnitude uncertainty -
switching to `RERANKER_PROVIDER=cross_encoder` would give
uncertainty_margin genuine score-magnitude meaning instead.

### What's deliberately NOT in Phase 8

- No automatic re-prioritization as feedback accumulates mid-session -
  `/prioritize` is a one-shot batch action; running it again recomputes
  every pending product's margin from scratch (idempotent, not
  incremental).
- No integration with Phase 7's model - uncertainty here is computed from
  Phase 2/6's existing reranked scores, not from a trained classifier's
  predicted probability (which doesn't exist yet in this project's
  current state - Phase 7's model was never deployed, per that phase's
  own notes).
- No per-user review-order personalization - one global priority order
  per upload, not per-reviewer.

This closes out all 8 phases from `B2B.md` section 36.

## Post-delivery: Quick Match Wizard tab + two real production bugs

Two things were added/fixed after the initial 8-phase delivery, once the
project moved from "built and tested" to "actually run against real
Postgres and real usage."

### Quick Match Wizard (standalone adapter)

The user supplied a pre-built, self-contained HTML wizard
(`frontend/public/matching-standalone.html` - one file, vanilla JS, no
build step, no dependency on this project's React setup) with its own
simple contract: preview two Excel files, run a background matching job,
poll it, walk a one-question-at-a-time review of only the ambiguous
items, then save and download a result file.

Rather than rewrite that UI to fit this project's existing API shape, a
thin backend adapter was written to match the UI's contract instead:

- `backend/app/services/standalone_matching.py` - an in-memory job
  registry (`JobState` + a lock-guarded dict, mirroring the polling
  pattern the HTML already expects), a classifier that reuses the real
  `matching.find_exact_match` / `matching.get_top_candidates` /
  confidence-threshold logic from Phase 5 (no separate matching logic was
  invented), a `save_results()` that writes real `Match`/`Feedback` rows
  (same tables Phase 3/4 use), and a real `openpyxl`-based export.
- `backend/app/api/standalone_matching.py` - a router at
  `/api/v1/matching/*` exposing exactly the four endpoints the HTML polls
  (`excel/preview`, `excel/run/start`, `jobs/{job_id}`, `save`).
- `frontend/app/page.tsx` gained a third tab ("Quick Match Wizard") that
  iframes the static HTML file. The two original tabs (`ReviewTab.tsx`,
  `StatsTab.tsx`) were left completely untouched, per explicit
  instruction - this is an additional workflow, not a replacement.

This means there are now two independent ways to review destination
products against the same underlying data: the original per-item
React review screen (Phase 3, best for reviewing everything one at a
time with full explanations), and the wizard (best for a quick
upload -> auto-classify -> only-review-the-ambiguous-ones -> export
loop, minus the fine-grained keyboard-shortcut UI).

### Real bug: identically-named products scoring only ~68-79%

Found via real usage: a destination product named "Грелка резиновая"
matched an identically-named master product at only 68% instead of
~100%. Root cause was in `backend/app/services/search/types.py` -
`MasterProductRecord.search_text()` concatenated `normalized_name +
description` for indexing, but destination-side queries are always built
from `normalized_name` alone (see `matching.get_top_candidates` /
`matching.try_auto_match`). Two products with an identical name but
different-length descriptions ended up with different indexed text,
diluting embedding/BM25 similarity asymmetrically - a bug invisible in
unit tests that never varied description length across identically-named
products. Fixed by making `search_text()` name-only; locked in by
`test_identical_names_score_highly_regardless_of_differing_descriptions`
in `backend/tests/test_index_manager.py`.

### Real bug: PostgreSQL VARCHAR truncation crashing ingestion

Found only after switching from SQLite (used throughout development and
testing in this sandbox) to real PostgreSQL via Docker: master catalog
ingestion crashed with `psycopg2.errors.StringDataRightTruncation`. Root
cause: the master catalog has section-header rows where a 300+ character
piece of text ends up mapped into the "Код" column, which maps to
`external_id` (`VARCHAR(255)`). SQLite has no such length enforcement, so
this had been silently "working" through every prior test run against
SQLite - a real limitation of validating this project only against
SQLite, flagged honestly rather than treated as fully proven.

Fixed in `backend/app/services/ingestion.py`:
- `_to_str_limited()` truncates `external_id`/`unit`/`freight_class` to
  their column's max length before insert, rather than crashing.
- `_flush_batch_with_row_fallback()` - if a batch insert still fails at
  the DB level (`DataError`/`IntegrityError`), rolls back and retries
  row-by-row, so one bad row can't take an entire 500-row batch down with
  it (per Phase 1's original "one malformed row never aborts a batch"
  principle - this closes a gap where that guarantee only held for
  Python-level validation errors, not DB-level constraint violations).

### Threshold tuning notes (real usage, not spec defaults)

Based on real review sessions, `HIGH_CONFIDENCE_THRESHOLD` and
`LOW_CONFIDENCE_THRESHOLD` were retuned away from the spec's literal
defaults:
- `HIGH_CONFIDENCE_THRESHOLD`: 0.95 -> 0.60 (the spec's 0.95 rarely fired
  given Phase 2's partial scoring ceiling - see Phase 5/2 notes above).
- `LOW_CONFIDENCE_THRESHOLD`: 0.15 -> 0.40, with
  `ENABLE_LOW_CONFIDENCE_AUTO_REJECT=true`, so obviously-hopeless
  candidates auto-reject instead of requiring a manual "None of these"
  click on every one. 0.70 was considered and rejected - it would have
  overlapped with the 0.60 accept threshold and left zero destination
  products for actual human review, which defeats the point of a review
  workflow. 0.40 keeps a real (smaller) review band between the two
  thresholds.
