# B2B Product Matching System — Complete Context Guide

**Last Updated:** July 28, 2026  
**Project:** AI-driven procurement product matching for Kazakhstan government organizations  
**Status:** All 8 phases complete + bonus features

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture at a Glance](#architecture-at-a-glance)
3. [What's Built](#whats-built)
4. [How to Setup & Run](#how-to-setup--run)
5. [Tech Stack](#tech-stack)
6. [Database Schema](#database-schema)
7. [API Endpoints](#api-endpoints)
8. [Common Workflows](#common-workflows)
9. [Troubleshooting](#troubleshooting)

---

## Project Overview

**What problem does it solve?**

Government organizations (Kazakhstan schools, hospitals, procurement departments) maintain two Excel files:
- **Master Catalog:** Official approved equipment/supplies list (5,000+ products with codes, prices, units)
- **Request/Destination List:** What they actually want to buy (2,000+ items with inconsistent naming, typos, duplicates)

**Task:** Automatically match destination items to master catalog items with high confidence.

**Why it's hard:**
- Product names in destination file have typos, abbreviations, extra adjectives
- Same product called by different names across files
- 10,000+ manual matches would take weeks

**Solution:** Hybrid AI matching system using:
- Keyword search (BM25)
- Fuzzy name matching
- Vector embeddings (semantic similarity)
- ML classifier trained on confirmed matches
- Human review UI for ambiguous cases

---

## Architecture at a Glance

```
User uploads Excel files
          ↓
[FastAPI Backend] ← → [PostgreSQL Database]
  - Read & normalize Excel
  - Store in database
  - Hybrid search (keyword + fuzzy + vector)
  - Machine learning training
          ↓
[Next.js Frontend]
  - Display matches to human reviewer
  - Collect feedback (confirm/reject)
  - Show statistics & confidence
          ↓
[Vector Database - Qdrant]
  - Store product embeddings
  - Enable semantic search
```

**Two test files included:**
- `Казниса апрель.xlsx` — Master catalog (~5,195 products)
- `Детсад.xlsx` — Destination file (~2,885 items)

---

## What's Built

### Phase 1: Data Pipeline ✅
- Upload Excel files (master or destination)
- Detect headers automatically
- Map raw column names to canonical fields (product_name, price, unit, etc.)
- Normalize text (lowercase, trim, Cyrillic character fixes, punctuation cleanup)
- Store in PostgreSQL with per-row error isolation
- Preserve original data as JSON for traceability

### Phase 2: Hybrid Search ✅
Three search methods combined:

1. **BM25 Keyword Search** — Find products by matching words (fast, index-based)
2. **Fuzzy Matching** — Handle typos and near-duplicates (e.g., "Столь" → "Стол")
3. **Vector Embeddings** — Semantic similarity using Qdrant (understands meaning)

Weights: 35% embedding + 25% keyword + 15% fuzzy + 10% category + 10% attributes + 5% identifier

### Phase 3: Review UI ✅
- Next.js + Tailwind CSS frontend
- Browse destination products one by one
- See top 3 candidates with explanation of why
- Confirm/reject/search manually
- Keyboard shortcuts (1/2/3 for top 3, N for none, Enter to confirm)
- Progress bar showing % matched

### Phase 4: Feedback Tracking ✅
- Every decision (confirm/reject) stored in `feedback` table
- Full candidate set preserved for learning
- Feedback stats endpoint shows breakdown by decision type

### Phase 5: Automatic Matching ✅
Two opt-in auto-match modes:

1. **Exact Match** — Same product code or identical normalized name → accept at 0.99 confidence
2. **Confidence Threshold** — Score > configurable threshold (default 0.95) → auto-accept

Auto-rejects low-confidence matches to save human time on obvious non-matches.

### Phase 6: Reranking ✅
Improve top-3 candidates before showing to human:

1. **RRF** (Reciprocal Rank Fusion) — Default, fast, offline-safe
2. **Cross-encoder** — Optional neural reranker for harder cases
3. **LLM Tie-breaker** — Use Claude API only for genuinely ambiguous pairs

### Phase 7: Supervised Learning ✅
Train a classifier after 500+ manual matches:

- Build labeled dataset: confirmed matches = positive, shown-but-rejected = negative
- Train GradientBoosting classifier (scikit-learn) or XGBoost/LightGBoost
- Features: embedding_similarity, bm25_score, fuzzy_score, price_difference
- Only deploy if improvement > margin (e.g., 5% better than baseline)
- `GET /api/ml/training-readiness` shows progress

### Phase 8: Uncertainty Prioritization ✅
Prioritize ambiguous matches for human review:

- Compute score gap between top-2 candidates (e.g., 0.51 vs 0.49 = ambiguous)
- Review high-uncertainty items first instead of top-to-bottom
- Focus human effort where it matters most

### Bonus: Quick Match Wizard ✅
Simplified one-file workflow:

1. Upload destination + master file
2. Backend auto-runs matching pipeline
3. One-question-at-a-time wizard (only needs-review items)
4. Export matched results as Excel (.xlsx)

---

## How to Setup & Run

### Prerequisites
- Docker installed
- Python 3.11+
- Node.js 16+

### 1. Start Database & Services (Docker)

```bash
cd C:\Users\hantishka\Claude\Projects\B2B

# Start PostgreSQL + Qdrant containers
docker compose up -d

# Verify running
docker ps
# Should see:
# - product_matching_db (PostgreSQL)
# - product_matching_qdrant (Qdrant vector DB)

# Check database exists
psql -h localhost -U postgres -d product_matching -c "\dt"
```

**Database credentials (from docker-compose.yml):**
- Host: `localhost`
- Port: `5432`
- User: `postgres`
- Password: `postgres`
- Database: `product_matching`

### 2. Backend Setup

```bash
cd backend

# Create & activate virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Optional: for ML training (scikit-learn, pandas, etc.)
pip install -r requirements-ml.txt

# Optional: for embeddings (sentence-transformers)
pip install -r requirements-embeddings.txt

# Check config
python -c "from app.config import settings; print(f'Database: {settings.DATABASE_URL}')"

# Start backend (port 8000)
python -m uvicorn app.main:app --reload --port 8000
```

Backend API: http://localhost:8000  
API Docs: http://localhost:8000/docs (Swagger)

### 3. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Start dev server (port 3000)
npm run dev
```

Frontend: http://localhost:3000

### 4. Load Test Data

**Option A: Via Web UI (recommended)**
1. Go to http://localhost:3000
2. Tab: "Upload & Review"
3. Upload `Казниса апрель.xlsx` as **Master Catalog**
4. Upload `Детсад.xlsx` as **Destination**
5. Click "Start Matching"

**Option B: Via CLI**
```bash
cd backend

python scripts/ingest_master.py ../Казниса\ апрель.xlsx "База КазНИИСА 04.2026"
python scripts/ingest_destination.py ../Детсад.xlsx "Список сводный д.сад"
```

### 5. Run Matching Workflow

1. **Upload files** (if not done via CLI)
2. **Review products:** Go to matching UI, confirm/reject each match
3. **Auto-match (optional):** `POST /api/matching/{upload_id}/auto-match` to match high-confidence items
4. **Prioritize (optional):** `POST /api/matching/{upload_id}/prioritize` to sort by uncertainty
5. **Train ML (after 500+ reviews):** `POST /api/ml/train` to build classifier

---

## Tech Stack

### Backend
- **Framework:** FastAPI (Python async web framework)
- **ORM:** SQLAlchemy 2.x (database abstraction)
- **Database:** PostgreSQL 16 (primary) or SQLite (fallback for testing)
- **Search:** 
  - BM25Okapi (rank-bm25 library)
  - rapidfuzz (fuzzy string matching)
  - Qdrant (vector similarity search)
- **ML:** scikit-learn (GradientBoosting), XGBoost optional
- **Embeddings:** sentence-transformers (multilingual BERT, optional)
- **Excel:** openpyxl, pandas (read-only, streaming)

### Frontend
- **Framework:** Next.js 14+ (React meta-framework)
- **Language:** TypeScript
- **Styling:** Tailwind CSS (utility-first CSS)
- **HTTP:** axios or fetch API

### Infrastructure
- **Containers:** Docker, docker-compose
- **Vector DB:** Qdrant (HNSW index)
- **Job Queue:** In-memory registry (can scale to Redis/Celery later)

---

## Database Schema

### Tables

#### `uploads`
Tracks each Excel file upload.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `filename` | text | Original filename |
| `upload_type` | text | `'master'` or `'destination'` |
| `sheet_name` | text | Which sheet was processed |
| `status` | text | `pending` → `processing` → `done` / `failed` |
| `total_rows` | int | Row count |
| `processed_rows` | int | Successfully parsed |
| `skipped_rows` | int | Malformed, skipped |
| `error_report` | JSON | List of `{row_number, reason}` |
| `created_at` | timestamp | Upload time |

#### `master_products`
Products from master catalog (source A).

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `upload_id` | UUID | Foreign key to `uploads` |
| `source_row` | int | Original row # for traceability |
| `external_id` | text | Product code (e.g., `521-101-0131-0001`) |
| `product_name` | text | Product name |
| `normalized_name` | text | Lowercased, trimmed, cleaned |
| `description` | text | Additional description |
| `unit` | text | Unit of measure (шт, м², кг, etc.) |
| `price` | numeric | Price in tenge |
| `freight_class` | text | Shipping classification |
| `gross_weight_kg` | numeric | Weight |
| `is_group_header` | bool | True if section/category header (no price) |
| `raw_data` | JSON | Full original row (untouched) |
| `created_at` | timestamp | Ingestion time |

#### `destination_products`
Items from destination/request file (source B).

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `upload_id` | UUID | Foreign key to `uploads` |
| `source_row` | int | Original row # |
| `external_id` | text | Item code (often blank) |
| `product_name` | text | Item name |
| `normalized_name` | text | Cleaned name |
| `description` | text | Details |
| `quantity` | numeric | Qty needed |
| `price` | numeric | Budget price |
| `status` | text | `pending` / `matched` / `no_match` |
| `uncertainty_margin` | numeric | Gap between top-2 candidates (Phase 8) |
| `raw_data` | JSON | Full original row |
| `created_at` | timestamp | |

#### `matches`
Confirmed matches (human-reviewed or auto-matched).

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `destination_product_id` | UUID | FK to destination_products |
| `master_product_id` | UUID | FK to master_products |
| `confidence` | float | Score 0.0–1.0 |
| `match_type` | text | `'confirmed'` / `'auto_matched'` / `'auto_rejected'` |
| `reviewed_by` | text | User who confirmed (optional) |
| `created_at` | timestamp | |

#### `feedback`
Training data for supervised learning (Phase 7).

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `destination_product_id` | UUID | FK |
| `candidate_master_product_id` | UUID | FK |
| `decision` | text | `'confirmed'` / `'rejected'` / `'none'` |
| `presented_rank` | int | Position in top-3 (1, 2, 3) |
| `final_score` | float | Matching score at decision time |
| `created_at` | timestamp | |

---

## API Endpoints

### Upload Management
```
POST   /api/uploads/master              Upload master catalog Excel
POST   /api/uploads/destination         Upload destination file Excel
GET    /api/uploads                     List previous uploads
GET    /api/uploads/{upload_id}         Get upload details
GET    /api/uploads/{upload_id}/sheets  List sheets in file
```

### Search
```
GET    /api/search/reindex              Rebuild search index from DB
```

### Matching Workflow
```
GET    /api/matching/{upload_id}/next              Get next unmatched product
POST   /api/matching/{upload_id}/confirm           Confirm top-N candidate
POST   /api/matching/{upload_id}/search            Manual search by query
POST   /api/matching/{upload_id}/auto-match        Run auto-matching
POST   /api/matching/{upload_id}/prioritize        Compute uncertainty scores
GET    /api/matching/{upload_id}/feedback-stats    Review stats
```

### Machine Learning
```
GET    /api/ml/training-readiness       How many more reviews needed for training?
POST   /api/ml/train                    Train new classifier
GET    /api/ml/model-info               Current model performance
```

### Standalone Matching (Quick Wizard)
```
POST   /api/v1/matching/excel/preview   Preview match results
POST   /api/v1/matching/excel/run/start Start background matching job
GET    /api/v1/matching/jobs/{job_id}   Check job progress
POST   /api/v1/matching/save            Save matches & export Excel
```

Full API docs: http://localhost:8000/docs (Swagger UI)

---

## Common Workflows

### Workflow 1: Basic Matching (Manual Review)

```bash
# 1. Upload files
curl -X POST http://localhost:8000/api/uploads/master \
  -F "file=@Казниса апрель.xlsx" \
  -F "sheet_name=База КазНИИСА 04.2026"

curl -X POST http://localhost:8000/api/uploads/destination \
  -F "file=@Детсад.xlsx" \
  -F "sheet_name=Список сводный д.сад"

# 2. Get first unmatched product
curl http://localhost:8000/api/matching/{upload_id}/next

# 3. Review top-3 candidates and confirm one
curl -X POST http://localhost:8000/api/matching/{upload_id}/confirm \
  -H "Content-Type: application/json" \
  -d '{"destination_product_id": "...", "master_product_id": "...", "rank": 1}'

# 4. Repeat until all matched
```

### Workflow 2: Auto-Matching + Human Review

```bash
# 1. Upload files (same as above)

# 2. Auto-match high-confidence items
curl -X POST http://localhost:8000/api/matching/{upload_id}/auto-match \
  -H "Content-Type: application/json" \
  -d '{"high_confidence_threshold": 0.90, "low_confidence_threshold": 0.30}'

# 3. Prioritize by uncertainty
curl -X POST http://localhost:8000/api/matching/{upload_id}/prioritize

# 4. Review remaining uncertain items (API will sort by uncertainty_margin)
curl "http://localhost:8000/api/matching/{upload_id}/next?strategy=uncertainty"
```

### Workflow 3: ML Training (After 500+ Reviews)

```bash
# 1. Check readiness
curl http://localhost:8000/api/ml/training-readiness
# Response: {"ready": true, "samples": 523, "required": 500}

# 2. Train classifier
curl -X POST http://localhost:8000/api/ml/train \
  -H "Content-Type: application/json" \
  -d '{"min_improvement_margin": 0.05}'

# 3. Check model performance
curl http://localhost:8000/api/ml/model-info
```

### Workflow 4: Quick Match Wizard (All-in-One)

```bash
# 1. Preview match results
curl -X POST http://localhost:8000/api/v1/matching/excel/preview \
  -F "master_file=@Казниса апрель.xlsx" \
  -F "master_sheet=База КазНИИСА 04.2026" \
  -F "destination_file=@Детсад.xlsx" \
  -F "destination_sheet=Список сводный д.сад"

# 2. Start background matching job
curl -X POST http://localhost:8000/api/v1/matching/excel/run/start \
  -F "master_file=@Казниса апрель.xlsx" \
  -F "master_sheet=База КазНИИСА 04.2026" \
  -F "destination_file=@Детсад.xlsx" \
  -F "destination_sheet=Список сводный д.сад"
# Response: {"job_id": "abc123"}

# 3. Poll for progress
curl http://localhost:8000/api/v1/matching/jobs/abc123

# 4. Save matches & download Excel
curl -X POST http://localhost:8000/api/v1/matching/save \
  -H "Content-Type: application/json" \
  -d '{"job_id": "abc123"}' \
  -o results.xlsx
```

---

## Troubleshooting

### Docker container not running
**Error:** `Connection refused` on port 5432

**Fix:**
```bash
docker compose up -d
docker ps  # Verify running
```

### pgAdmin4 can't see database
**Error:** No database listed in pgAdmin4

**Fix:**
1. Register new server in pgAdmin4:
   - Host: `localhost`
   - Port: `5432`
   - User: `postgres`
   - Password: `postgres`
2. Expand Servers → Databases → Should see `product_matching`

### Backend can't connect to database
**Error:** `psycopg2.OperationalError: could not connect to server`

**Fix:**
1. Check DATABASE_URL in `backend/.env` is correct
2. Verify Docker container is running: `docker ps | grep product_matching_db`
3. Test connection: `psql -h localhost -U postgres -d product_matching -c "SELECT 1"`

### No embeddings generated (vector search not working)
**Error:** Qdrant returns empty results

**Fix:**
1. Check `EMBEDDING_PROVIDER` in `backend/app/config.py`
2. If `sentence-transformers`, ensure internet access to download model
3. If using TF-IDF fallback, check Qdrant is running: `docker ps | grep qdrant`
4. Reindex: `curl -X POST http://localhost:8000/api/search/reindex`

### Frontend can't reach backend
**Error:** CORS error or "API endpoint not found"

**Fix:**
1. Verify backend is running: `http://localhost:8000/docs` should be accessible
2. Check `frontend/.env.local` has correct `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`
3. Restart frontend: `npm run dev`

### ML training fails with "Not enough samples"
**Error:** `POST /api/ml/train` returns 400

**Fix:**
1. Need minimum 500 feedback samples
2. Check progress: `curl http://localhost:8000/api/ml/training-readiness`
3. Perform more manual reviews in UI
4. Once ready, retry training

### Excel upload fails / partial ingestion
**Error:** Only some rows processed, others in error_report

**Fix:**
1. Check column headers in Excel match expected format
2. Run: `curl http://localhost:8000/api/uploads/{upload_id}` to see error_report
3. Fix Excel formatting, re-upload
4. Per-row errors are isolated (won't fail entire upload)

---

## Key Files to Know

| File | Purpose |
|------|---------|
| `backend/app/main.py` | FastAPI app entry point, router registration |
| `backend/app/models.py` | SQLAlchemy ORM models (Upload, MasterProduct, etc.) |
| `backend/app/services/ingestion.py` | Upload → normalize → store pipeline |
| `backend/app/services/column_mapper.py` | Map Excel headers to canonical fields |
| `backend/app/services/normalizer.py` | Text normalization (lowercase, trim, etc.) |
| `backend/app/services/search/hybrid_search.py` | BM25 + fuzzy + vector combination |
| `backend/app/services/ml/trainer.py` | Supervised learning pipeline |
| `frontend/app/page.tsx` | Main React component (three tabs) |
| `frontend/public/matching-standalone.html` | Quick Match Wizard (iframe) |
| `docker-compose.yml` | PostgreSQL + Qdrant containers |
| `ARCHITECTURE.md` | Deep-dive technical design |
| `README.md` | Feature overview |
| `ROADMAP.md` | Next phases (Phase 3 enhancements) |

---

## Quick Reference: Environment Variables

**Backend** (`backend/.env` or `.env.example`):
```env
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/product_matching
QDRANT_URL=http://localhost:6333
UPLOAD_LIMIT_MB=500
EMBEDDING_PROVIDER=tfidf  # or sentence-transformers
HIGH_CONFIDENCE_THRESHOLD=0.90
LOW_CONFIDENCE_THRESHOLD=0.30
ML_MODEL_BACKEND=sklearn  # or xgboost, lightgbm
ANTHROPIC_API_KEY=sk-...  # Optional, for LLM reranker
```

**Frontend** (`frontend/.env.local`):
```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

---

## Next Steps

1. **Setup:** Follow "How to Setup & Run" section
2. **Test:** Upload test files, review matches via UI
3. **Extend:** See ROADMAP.md for Phase 3 features (Kazakh LLM, data quality scoring)
4. **Deploy:** Docker images ready for cloud deployment

---

**Questions?** Check ARCHITECTURE.md for technical deep-dives or README.md for feature details.
