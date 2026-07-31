# B2B Data Management Platform
## Final Application Roadmap

**Target Completion:** Monday, July 27, 2026 (Single-Day Sprint)

---

## Project Overview

A comprehensive B2B data management solution designed to handle Excel-based procurement and organizational data with intelligent processing, normalization, and search capabilities. Built with FastAPI backend, Next.js frontend, and PostgreSQL/SQLite database.

---

## Current Architecture

| Layer | Components |
|-------|-----------|
| **Backend** | FastAPI, SQLAlchemy, Excel processing (openpyxl), BM25/Fuzzy search |
| **Database** | SQLAlchemy ORM, PostgreSQL/SQLite support |
| **Frontend** | Next.js, TypeScript, Tailwind CSS |
| **Services** | Column mapping, Data normalization, Excel reading, Hybrid search |

---

## Implementation Roadmap — Phase 3 (One-Day Sprint)

### 1. Visible Database for Sourcesheet

**Objective:** Expose underlying source data through a dedicated UI component for transparency.

**MVP Deliverables (4–5 hours):**
- Backend endpoint: `GET /api/sourcesheet` — paginated query with filtering
- React component: Table view with sort/filter by upload_id, sheet_name, status
- Basic export functionality (JSON/CSV)
- ⚠️ Defer: Advanced analytics, audit trails

**Effort:** 4–5 hours | **Priority:** High

---

### 2. Excel Table Normalization Enhancement

**Objective:** Improve data digestion quality through core normalization rules.

**MVP Deliverables (4–5 hours):**
- Add Kazakh character normalization (ё↔е, soft sign handling)
- Schema validation: detect and flag type mismatches
- Row-level error reporting (missing required fields)
- Display normalization score in upload status UI
- ⚠️ Defer: Smart header detection, duplicate detection, full morphological analysis

**Effort:** 4–5 hours | **Priority:** High

---

### 3. Kazakh Language LLM Integration (Foundation)

**Objective:** Establish foundation for Kazakh language support; full integration in follow-up sprint.

**MVP Deliverables (3–4 hours):**
- Select & document Kazakh LLM (KazBERTA or Gigachat API)
- Integrate with existing search pipeline (API call + caching structure)
- Add Kazakh tokenizer to preprocessing
- Proof-of-concept: 1–2 Kazakh search queries returning results
- Set up Qdrant placeholder for embeddings
- ⚠️ Defer: Full embedding generation, semantic search optimization, morphological analysis

**Effort:** 3–4 hours | **Priority:** High (Foundation)

---

## Technical Priorities & One-Day Breakdown

| Task | Priority | Duration | Focus |
|------|----------|----------|-------|
| Database endpoint + simple UI | High | 2 hours | Endpoint + basic React table |
| Normalization rules + UI display | High | 2 hours | Character fixes + validation scoring |
| Kazakh LLM setup + POC | High | 2 hours | Model selection + API integration |
| Testing & bug fixes | Medium | 2 hours | E2E test, integration checks |

---

## Success Criteria

- ✓ Source database is browsable and filterable in UI
- ✓ Data quality scores visible after each upload
- ✓ Kazakh language search queries return relevant results
- ✓ All existing tests pass; new tests ≥90% coverage
- ✓ LLM response latency <500ms for typical queries

---

## Implementation Notes

- Existing infrastructure (column_mapper.py, normalizer.py, hybrid_search.py) provides a strong foundation for these features.
- Kazakh language support is critical—select a model with proven Kazakh handling (e.g., KazBERTA from Issai Lab or Gigachat API with Kazakh support).
- Vector embeddings should be cached in Qdrant to ensure sub-500ms query latency.
- All three features should be developed in parallel to meet the July 27 deadline.

---

**Document prepared:** July 24, 2026  
**For:** Mentor review
