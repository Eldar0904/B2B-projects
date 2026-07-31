# AI Product Matching System

Full spec: `B2B.md`. This repo implements all **8 phases** from `B2B.md`
section 36: Data Pipeline, Search, Matching UI, Feedback, Automatic
Matching, Reranking, Supervised Learning, and Active Learning.

## What's built

**Phase 1**: Excel upload -> read -> column mapping -> text normalization ->
stored in PostgreSQL, with per-row error isolation so one malformed row
never aborts a batch.

**Phase 2**: hybrid retrieval over the master catalog - BM25 keyword
search, fuzzy name matching, and vector similarity (Qdrant) - combined
into a single weighted, configurable score per destination product.

**Phase 3**: the review workflow - destination product -> top 3 candidates
-> user selection, per spec section 36 ("usable before adding advanced
ML"). Backend endpoints under `/api/matching/{upload_id}/...` plus a
Next.js + TypeScript + Tailwind frontend (`frontend/`) implementing the
review screen: progress bar, candidate cards with real explanations
("why this match?"), Confirm / None of these / Search manually, and
keyboard shortcuts (1/2/3 select, N none, Enter confirm).

**Phase 4**: every decision (confirm from top-3, confirm from manual
search, reject/none) now writes a `feedback` row with the full candidate
set shown, per spec section 20 - not just a status update. A
`GET /api/matching/{upload_id}/feedback-stats` endpoint reports counts by
decision type, a precursor to Phase 7's "500+ verified matches" training
threshold.

**Phase 5**: two independent sources of automatic matching, both opt-in
via `POST /api/matching/{upload_id}/auto-match`:
- **Exact match** (spec section 11) - identical product code or identical
  normalized name auto-accepts at 0.99 confidence, independent of the
  hybrid score.
- **Confidence threshold** (spec section 16) - hybrid `final_score` above
  a configurable threshold auto-accepts. See `ARCHITECTURE.md` for why the
  spec's literal 0.95 default rarely fires yet (Phase 2's scoring is still
  partial) and why that's the honest, intentional behavior rather than an
  unvalidated threshold tuned to make a demo look better.

  `GET /api/matching/{upload_id}/next` now also reports a
  `confidence_level` (high/medium/low) and `no_reliable_match` flag per
  spec section 16, and the frontend shows an auto-match button plus a "no
  reliable match" banner when relevant.

**Phase 6**: the top-20 pool from Phase 2 gets reranked before truncating
to the top-3 a human sees, per spec section 15. Three interchangeable
rerankers:
- **RRF** (default, offline-safe) - Reciprocal Rank Fusion across the
  keyword/fuzzy/vector sub-scores, a real technique (same one
  Elasticsearch/Qdrant use natively for fusion), not a placeholder.
- **Cross-encoder** (optional, spec's "Option A") - a real multilingual
  cross-encoder model; same optional-dependency story as Phase 2's
  embeddings (needs `requirements-embeddings.txt` + network access, not
  exercised against real data in this sandbox for that reason).
- **LLM tie-breaker** (optional, spec's "Option B", for "hard" cases only)
  - only called when the base reranker's top-2 candidates are genuinely
  ambiguous; needs `ANTHROPIC_API_KEY`, off by default, and was tested
  with a mocked client rather than a real API call here.

**Phase 7**: the training pipeline for a supervised `P(match)` classifier,
per spec section 21-23. `POST /api/ml/train` builds a labeled dataset from
stored `feedback` (confirmed matches = positive, every other candidate
shown-but-not-picked = a hard negative), trains a model, and evaluates it
against Phase 2's existing linear scoring as a baseline - deploying only
if it clears a configurable improvement margin. Two things worth reading
before using this:
- The default model is scikit-learn's `GradientBoostingClassifier`, not
  XGBoost/LightGBM as spec section 23 names - `pip download` for the
  xgboost wheel returned zero bytes after repeated attempts in this
  sandbox (likely an environment-specific transfer restriction, not a
  code problem). Both are supported as optional backends
  (`ML_MODEL_BACKEND=xgboost` or `lightgbm`) for environments where they
  install normally.
- The feature set is honestly smaller than spec section 23's list: only
  `embedding_similarity`, `bm25_score`, `fuzzy_name_score`, and
  `price_difference` are computed. `category_match`, `brand_match`,
  `unit_match`, and the rest aren't available because attribute
  extraction/category classification were never built in any earlier
  phase (and destination products never even captured a `unit` field -
  see `ARCHITECTURE.md`).
- `GET /api/ml/training-readiness` reports real progress toward the
  500-example threshold; `POST /api/ml/train` refuses to train below it
  and says exactly how many more examples are needed, rather than
  training on whatever's available.

**Phase 8**: uncertainty-based review prioritization, per spec section 24.
`POST /api/matching/{upload_id}/prioritize` computes, for every pending
destination product, the score gap between its top-2 reranked candidates
(spec's own example: `0.51` vs `0.49` = highly uncertain, prioritize for
a human; `0.99` vs `0.32` = confident) and stores it. `GET
/api/matching/{upload_id}/next?strategy=uncertainty` then reviews the
most ambiguous products first instead of just working through the file
top to bottom, focusing human attention where it adds the most value.
Worth knowing: with the default RRF reranker, this margin measures
*rank-agreement* uncertainty rather than *score-magnitude* uncertainty -
see `ARCHITECTURE.md` for a real caveat found while testing this (a small
synthetic catalog produced identical margins for a genuinely ambiguous
case and an unambiguous one, until the catalog had enough unrelated
products for rank consistency to actually differ between the two).

**Quality-of-life additions made after initial delivery**, based on real
usage friction:
- **Low-confidence auto-reject** - `POST /api/matching/{upload_id}/auto-match`
  now also auto-marks items `no_match` when even the best candidate is
  clearly not a match (`LOW_CONFIDENCE_THRESHOLD`, opt-in via
  `ENABLE_LOW_CONFIDENCE_AUTO_REJECT`), so a human doesn't have to
  manually click "None of these" on the obviously hopeless ones. Uses its
  own `auto_rejected` decision type (distinct from a human's `no_match`)
  so feedback stats can tell them apart.
- **`GET /api/uploads`** - lists previous uploads (optionally filtered by
  `upload_type`), so you can find and reuse a previous destination
  upload's id instead of re-uploading the same file every session. The
  frontend now shows a "pick a previous upload" list on the start screen
  and remembers your last-used upload id in the browser.
- **Auto-rebuild the search index on backend startup** - previously,
  every restart wiped the in-memory search index and required a manual
  `POST /api/search/reindex` before anything would work. Now, if a master
  catalog already exists in the database, the index rebuilds
  automatically on startup - no manual step needed to pick back up where
  you left off.

**Quick Match Wizard tab** (added after initial delivery): a third frontend
tab, alongside the original "Upload & Review" and "Stats & Training" tabs
(neither was touched - both work exactly as before). It embeds a
self-contained, one-file HTML wizard (`frontend/public/matching-standalone.html`)
via an iframe, giving a simpler alternate workflow for the same backend:

1. Drop in a destination ("Заявка") Excel file and a catalog Excel file.
2. The backend re-ingests both fresh, rebuilds the search index, and
   classifies every destination product into auto-matched / needs-review /
   no-match (same `HIGH_CONFIDENCE_THRESHOLD` / `LOW_CONFIDENCE_THRESHOLD`
   logic as the main auto-match endpoint), all as one background job
   polled via `GET /api/v1/matching/jobs/{job_id}`.
3. A one-question-at-a-time wizard walks through only the needs-review
   items - pick a candidate or "not suitable" for each.
4. A summary screen with per-bucket previews, then **Save**, which calls
   `POST /api/v1/matching/save`: writes real `Match`/`Feedback` rows (same
   tables Phase 3/4 use) and returns a generated `.xlsx` (Destination ID,
   Destination Product, Matched Master ID, Matched Product, Confidence,
   Match Type, Reviewed) that downloads automatically.

New backend router: `backend/app/api/standalone_matching.py`
(`/api/v1/matching/excel/preview`, `/excel/run/start`, `/jobs/{job_id}`,
`/save`), backed by `backend/app/services/standalone_matching.py` (an
in-memory job registry + classification/save/export logic reusing the
existing ingestion, search, and matching services rather than duplicating
them). 8 new tests in `backend/tests/test_standalone_matching.py`.

Full design decisions: `ARCHITECTURE.md`, `DATABASE.md`.

Validated end-to-end against the two real files in this folder:

- `Казниса апрель.xlsx` (sheet `База КазНИИСА 04.2026`) - master catalog,
  5,194 data rows, all ingested with 0 errors. 168 rows are category/group
  headers (no unit or price), flagged `is_group_header=true` and excluded
  from search rather than dropped.
- `Детсад.xlsx` (sheet `Список сводный д.сад`) - destination file, 1,233
  real data rows (the sheet also contains 1,651 blank rows, correctly
  skipped), all ingested with 0 errors.
- Search: indexed all 5,026 non-header master products and ran retrieval
  for real destination products. Exact-ish matches rank cleanly at the top
  (e.g. "Холодильный шкаф 1 дверь vsp-1" -> "Шкаф холодильный", score 1.0
  keyword / 0.70 fuzzy). Weaker matches show up when the master catalog
  genuinely has no close item (e.g. a "световой стол" / light table has no
  equivalent in this particular catalog) - that's the retrieval pipeline
  working correctly against real, imperfect data, not a bug. See
  `ARCHITECTURE.md` for the known limitation of the offline TF-IDF
  embedding fallback vs. a real multilingual model in production.
- Matching workflow: ran the full review loop against the real ingested
  data - fetched next-pending destination products in order, retrieved
  top-3 candidates with explanations, confirmed matches, and verified
  progress counters update correctly.
- Feedback: reviewed 4 real destination products (2 confirmed from top-3,
  1 rejected, 1 confirmed via manual search) and verified
  `feedback-stats` came back exactly as expected
  (`user_selected=2, no_match=1, manual_search_selected=1`), with each
  `feedback` row carrying the full candidate set that was shown.
- Auto-matching: found 36 of the 1,233 real destination products (≈2.9%)
  have an exact normalized-name match in the master catalog. Ran the real
  auto-match function against a slice of them and confirmed it correctly
  found and auto-accepted the exact match, updated progress/feedback
  stats, and left everything else pending - exactly as designed.
- Reranking: compared raw retrieval order against RRF-reranked order for
  several real destination products. RRF preserved an already-correct top
  match in a clean case, but also reordered candidates in some
  low-confidence cases in ways that aren't obviously an improvement - a
  known, documented trade-off of RRF (it fuses rank, not confidence
  magnitude). See `ARCHITECTURE.md` for the full discussion, including a
  scale-mismatch caveat found while testing the LLM tie-breaker's
  ambiguity threshold.
- Supervised learning: reviewed 15 real destination products to generate
  real feedback, producing 45 labeled training pairs (10 positive, 35
  hard-negative). Confirmed `POST /api/ml/train` correctly refused to
  train on that real data ("need 455 more") rather than training on an
  inadequate sample - the gate works exactly as designed.
- Active learning: prioritized 100 real pending destination products in
  1.6 seconds and confirmed the uncertainty-ordered queue surfaced
  genuinely ambiguous, broad-category items (e.g. "Компьютерная техника и
  устройства") ahead of the plain sequential order.
- Frontend: `npm install`, `tsc --noEmit`, and `next build` all pass
  cleanly with zero errors.

## Running it

### Option A: Docker - one command (recommended)

`docker compose up --build` starts everything - Postgres, Qdrant, the
FastAPI backend, and the Next.js frontend - wired together on one Docker
network, with hot reload for both backend and frontend.

**Prerequisites:** Docker Desktop (or Docker Engine + the `docker compose`
plugin) installed and running. Nothing else needs to be installed on your
machine - Python, Node, and all dependencies live inside the containers.

**Setup (first time only):**

```bash
cp backend/.env.example backend/.env      # edit if you want non-default settings
cp frontend/.env.local.example frontend/.env.local
```

Nothing in `backend/.env` needs to change to run under Docker -
`docker-compose.yml` always forces `DATABASE_URL` and `QDRANT_URL` to the
correct container-network addresses regardless of what's in that file
(see the comments in `docker-compose.yml`), so the same `backend/.env`
works whether you run natively or in Docker.

**Start everything:**

```bash
docker compose up --build
```

First run downloads images and installs dependencies inside the
containers, so it can take a few minutes. Subsequent runs are much
faster. Watch for `product_matching_backend` logging
`Application startup complete` and `product_matching_frontend` logging
`Ready` - once both are up, open **http://localhost:3000**.

**Stop everything:**

```bash
docker compose down
```

**Rebuild after changing dependencies** (`requirements.txt` /
`package.json`) or Dockerfiles:

```bash
docker compose down
docker compose up --build
```

Editing application source code (`.py`/`.tsx` files) does **not** require
a rebuild - both containers bind-mount your source and hot-reload
automatically (`uvicorn --reload` for the backend, `next dev` for the
frontend).

**Important:** if you were previously running the backend/frontend
natively (outside Docker) or had `docker compose up -d` running for just
Postgres+Qdrant from before, stop those first - they use the same ports
(5432, 6333, 8000, 3000) and will conflict.

**Required/relevant environment variables** (all optional - sensible
defaults are baked into `docker-compose.yml` and `backend/.env.example`):

| Variable | Where | Purpose |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | root `.env` (optional) | Override Postgres credentials/db name (defaults: `postgres`/`postgres`/`product_matching`) |
| `ANTHROPIC_API_KEY` | `backend/.env` | Only needed if `ENABLE_LLM_RERANKER_FOR_HARD_CASES=true` (Phase 6) |
| Everything else in `backend/.env.example` | `backend/.env` | Search/matching/ML tuning - see the file's comments |

No secrets are hardcoded anywhere in `docker-compose.yml` or the
Dockerfiles - `backend/.env` (gitignored) is loaded at container start via
`env_file:`, never baked into an image layer.

### Option B: Manual (without Docker)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp ../.env.example ../.env   # edit if needed
docker compose -f ../docker-compose.yml up -d postgres qdrant   # just the databases

uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
cp .env.local.example .env.local   # points at the backend, defaults to localhost:8000
npm run dev
```

Then, via the API:

```bash
# Phase 1
POST /api/uploads/master        # upload + ingest the master catalog
POST /api/uploads/destination   # upload + ingest a destination file
GET  /api/uploads/{id}/status
GET  /api/uploads                              # list previous uploads (optional ?upload_type=master|destination)

# Phase 2
POST /api/search/reindex                       # build keyword/fuzzy/vector indexes from current master products
GET  /api/search/candidates/{destination_id}    # top-20 hybrid candidates for one destination product

# Phase 3 + 4 + 5 + 6 + 8
POST /api/matching/{upload_id}/start            # upload_id = the destination upload's id
GET  /api/matching/{upload_id}/progress
POST /api/matching/{upload_id}/auto-match       # batch: auto-accept exact/high-confidence, auto-reject low-confidence, opt-in
POST /api/matching/{upload_id}/prioritize       # batch: compute uncertainty margins for pending products
GET  /api/matching/{upload_id}/next?strategy=sequential|uncertainty   # next product + reranked top-3
POST /api/matching/{upload_id}/confirm          # {destination_product_id, master_product_id, rank} -> writes Match + Feedback
POST /api/matching/{upload_id}/reject           # {destination_product_id} -> writes Feedback (no_match)
POST /api/matching/{upload_id}/manual-search    # {query} - full-catalog search, not limited to top-3
GET  /api/matching/{upload_id}/feedback-stats   # counts of stored decisions by type

# Phase 7
GET  /api/ml/training-readiness                 # feedback volume vs the 500-example threshold
POST /api/ml/train                              # trains + evaluates against baseline if enough data
```

Open `http://localhost:3000` - the start screen now remembers your last
upload id and lists previous destination uploads you can pick from
directly, so you don't need to re-upload files or hunt for an id every
time you reopen the app. Click Start. Use "Prioritize by uncertainty" to
switch the review order to most-ambiguous-first, and "Run auto-match" to
auto-accept exact/high-confidence matches (and auto-reject clearly-wrong
ones, if enabled) before reviewing the rest by hand.

Or from the command line, without running the server:

```bash
python ../scripts/ingest_master.py "../Казниса апрель.xlsx" --sheet "База КазНИИСА 04.2026"
python ../scripts/ingest_destination.py "../Детсад.xlsx" --sheet "Список сводный д.сад"
```

No Docker/Postgres/Qdrant available? Set `DATABASE_URL=sqlite:///./product_matching.db`
in `.env` and leave `QDRANT_URL` unset (Qdrant falls back to embedded
local-file mode automatically) - the same code path works against both,
and is what was used to validate this project in this sandbox.

## Tests

```bash
cd backend
pytest tests/ -v
```

121 tests (120 passing, 1 skipped - the cross-encoder import guard, since
`sentence-transformers` isn't installed by default): column mapping, text
normalization, Excel reading (sheet/header detection, blank-row
skipping), ingestion (malformed rows, group-header detection, long
values that would overflow a PostgreSQL VARCHAR column, batch-flush
row-fallback isolation), keyword/fuzzy/vector search (including that
identically-named products score identically regardless of differing
descriptions), embeddings, hybrid-search scoring/combination, the
matching workflow (progress counting, next-product retrieval,
confirm/reject, explanation generation), feedback storage (decision
types, candidate JSON shape, stats), auto-matching (exact match,
threshold match, low-confidence auto-reject, confidence classification,
batch processing), reranking (RRF correctness, LLM tie-breaker with a
mocked client, ambiguity-threshold behavior), supervised learning
(dataset generation, feature extraction, model training, the
500-example gate, the deploy-only-if-improves evaluation), active
learning (uncertainty margin computation, prioritization batch, both
review-queue strategies), the uploads list endpoint, the startup
auto-reindex behavior, and the standalone matching wizard adapter
(job registry, classification, save/export, Excel generation).

## What's next (beyond the spec's 8 phases)

All 8 phases from `B2B.md` section 36 are implemented. Natural follow-ons
that weren't part of the spec's phase plan but came up repeatedly in the
architecture notes:

- Attribute extraction (spec section 7) and category classification
  (spec section 8) - neither was built in any phase, which is why
  Phase 2's scoring formula and Phase 7's feature set are both smaller
  than the spec's full vision. This is the highest-leverage next step.
- Wiring a Phase 7 model that passes evaluation into the live scoring
  path (currently training/evaluation is a reporting tool, not connected
  to matching decisions).
- An upload UI in the frontend - destination upload ids can now be picked
  from a list of previous uploads, but there's still no in-browser
  file-picker to upload a brand-new Excel file without going through
  `/docs`.
- The `match_candidates` table exists in `backend/app/models.py` for
  forward compatibility but nothing writes to it (candidate pools are
  recorded inline inside each `feedback` row's JSON instead, which has
  been sufficient so far).

For production use:
- Switch the embedding backend from the offline TF-IDF fallback to the
  real multilingual model, and consider the cross-encoder reranker too:
  `pip install -r backend/requirements-embeddings.txt` then set
  `EMBEDDING_PROVIDER=sentence-transformers` and/or
  `RERANKER_PROVIDER=cross_encoder` in `.env`.
- Re-evaluate `HIGH_CONFIDENCE_THRESHOLD` once real embeddings (and
  later, category/attribute signals) raise the achievable `final_score`
  ceiling above Phase 2's current ~0.75 partial-scoring cap.
- If enabling the LLM tie-breaker (`ENABLE_LLM_RERANKER_FOR_HARD_CASES`),
  set `ANTHROPIC_API_KEY` and re-check `LLM_AMBIGUITY_THRESHOLD` against
  whichever base reranker you're using - RRF and cross-encoder scores
  live on very different scales (see `ARCHITECTURE.md` Phase 6 caveat).
- Try `ML_MODEL_BACKEND=xgboost` or `lightgbm` once you have 500+ real
  verified matches - a normal (non-sandboxed) environment should have no
  trouble installing either.

## Known environment quirk (this sandbox only)

While building this, file writes to this project folder occasionally got
silently truncated mid-write in this particular sandboxed environment
(unrelated to the code itself), and large binary package downloads
(the xgboost wheel) failed outright. Every file was re-verified afterward
(parsed with `ast.parse` / re-read) and the full test suite re-run clean.
If you ever see something that looks truncated or odd, it's worth a
second look, but as of this delivery every `.py`/`.ts`/`.tsx` file parses
correctly, all 108 backend tests pass, and the frontend builds cleanly.
Separately, a stray, partially-corrupted `frontend/node_modules/` may be
left behind - it's gitignored and harmless; deleting it and running
`npm install` fresh will clear it.
