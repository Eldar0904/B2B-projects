# Matching Engine v2 — what was broken, what changed, and how FastAPI works

Written July 2026. Every number below was measured against your two real
files, `Казниса апрель.xlsx` (5,163 catalog rows) and `Детсад.xlsx`
(1,214 request rows).

---

## 1. Why it wasn't matching

The system was not "a bit inaccurate". Four separate defects compounded,
and together they made a correct match nearly impossible to distinguish
from a coincidence.

### 1.1 The score could never exceed 0.75

`scoring.py` split the final score across six signals:

```
embedding 0.35 + keyword 0.25 + fuzzy 0.15
  + category 0.10 + attribute 0.10 + identifier 0.05
```

The last three were never implemented and always returned `0.0`. So a
*perfect* match on every working signal scored 0.75, while the configured
auto-accept threshold was 0.95. The hybrid auto-accept path had therefore
never fired a single time since the system was built. The only automation
that ever worked was the exact-string shortcut, which covers 2.8% of your
destination rows.

Your `.env` had been hand-lowered to `HIGH=0.60 / LOW=0.40` to compensate.
That produced the *opposite* failure, described next.

### 1.2 Half your data was being silently discarded

With `ENABLE_LOW_CONFIDENCE_AUTO_REJECT=true` and `LOW_CONFIDENCE_THRESHOLD=0.40`,
anything scoring under 0.40 was marked `no_match` without a human ever
seeing it. On a 300-row sample:

| outcome | share |
|---|---|
| auto-matched | 9.7% |
| left for human review | 37.6% |
| **silently auto-rejected** | **52.7%** |

That is the single biggest reason the two spreadsheets appeared not to
match. Over half the rows were thrown away before anyone looked at them.

### 1.3 BM25 scores were fake

`keyword_search.py` divided every result by the best result *for that
query*:

```python
max_score = ranked[0][1]
return [(r.id, s / max_score) ...]
```

This guarantees the top hit always scores exactly 1.0, however bad it is.
A real example from your files:

```
query:   "Обучающие плакаты для дошкольников"
top hit: "Оборудование для единоборств"     keyword_score = 0.93
```

The only word these share is the stopword «для». The keyword signal
carried no information about match *quality* — only about ordering within
a single query.

### 1.4 The embedding space had collapsed

The default embedding was `TfidfVectorizer(analyzer="char_wb", ngram_range=(2,4), max_features=256)`.
256 character n-grams cannot represent a 5,000-product Cyrillic catalog:
almost every product projects onto the same handful of common bigrams, so
everything looks similar to everything.

```
"Ертегілер. Өзіміз оқимыз"   ~  "Тележка грузовая М без сумки"    0.34
"Динозаврлар. энциклопедия"  ~  "Датчик давления"                 0.39
```

### 1.5 Two more, found along the way

**The normalizer never stripped punctuation** despite its docstring saying
it did. `«3Д ұшбұрыш» (Интеллектум)` reached the matcher with its
guillemets and brackets intact.

**The fuzzy scorer was the wrong one for this data.** Your destination
names average 32 characters; catalog names average 93, because the catalog
appends full specifications. `token_sort_ratio` penalizes that length gap
severely:

| destination | catalog | `token_sort_ratio` | `token_set_ratio` |
|---|---|---|---|
| манеж детский | манеж детский размерами 830х680 мм | 55 | **100** |
| грелка резиновая | грелка резиновая объемом 2 л | 73 | **100** |

The catalog entry containing the destination name *verbatim* was scored
55% similar and pushed out of the top 3.

---

## 2. What changed

### 2.1 A new signal: IDF-weighted coverage

This is the most important addition. `app/services/search/lexical_overlap.py`
answers a question none of the other three signals could:

> Is the *important* part of the request actually accounted for by this
> candidate?

For query Q and candidate C it computes the fraction of Q's information
content (IDF mass, not word count) that appears in C. Common catalog words
like «стол» contribute little; distinctive ones like «дарсонвализации»
dominate.

The effect on the earlier false positive:

| pair | old score | new score |
|---|---|---|
| «Обучающие плакаты» vs «Оборудование для единоборств» | 0.479 (shown as top suggestion) | 0.303 |
| «Стеллаж (открытый)» vs «Шкаф стеллаж открытый 849х360х1835» | 0.517 | **0.912** |

### 2.2 Everything else

| file | change |
|---|---|
| `normalizer.py` | Actually strips punctuation. Kazakh letters `әғқңөұүһі` added to the token class. Dimensions unified (`900*900` = `900х900` = `900x900`). Domain stopwords. |
| `keyword_search.py` | Absolute saturating normalization instead of divide-by-best-hit. Stopwords removed. |
| `fuzzy_search.py` | `token_set_ratio` + `WRatio`, taking the better of the two. |
| `embeddings.py` | LaBSE is now the default. TF-IDF fallback rebuilt: word + character n-grams over the full vocabulary, projected with TruncatedSVD instead of truncated to 256 features. |
| `scoring.py` | Weights cover only implemented signals and sum to exactly 1.0, asserted at import. |
| `config.py` / `.env` | `HIGH=0.88`, `MEDIUM=0.55`, auto-reject **off**. |

### 2.3 Results

| metric | before | after |
|---|---|---|
| ground-truth top-1 | 94.1% | **97.1%** |
| ground-truth top-3 | 100% | 100% |
| true-match score (mean) | ~0.55 | **0.978** |
| true-match score (10th pct) | — | **0.949** |
| non-match score (mean) | 0.402 | 0.449 |
| non-match score (90th pct) | — | 0.718 |
| **silently auto-rejected** | **52.7%** | **0%** |
| test suite | 155 tests | **157 passing** (+ 3 new regression files) |

The gap between the true-match 10th percentile (0.949) and the non-match
90th percentile (0.718) is what makes a threshold meaningful. Previously
those two distributions overlapped almost completely, which is why no
threshold value could have worked.

### 2.4 About Kazakh — an important finding

You asked for a Kazakh LLM so the system could understand Kazakh text.
Worth knowing before you invest in that:

- **12.4%** of your destination rows contain Kazakh-specific letters.
- **0.3%** of your catalog rows do.

Those Kazakh items — `«Пішіндер» (Интеллектум)`, `Ертегілер. Өзіміз оқимыз`,
`Динозаврлар энциклопедия` — are mostly children's books and educational
materials that **are not in the КазНИИСА catalog at all**. No model can
match them, because there is nothing to match them to.

The correct behaviour is to confidently report "not in catalog" rather
than force a bad match, and that is what v2 now does: those rows score
0.19–0.38 instead of a misleading 0.45–0.50.

LaBSE still earns its place — it handles genuine Kazakh↔Russian synonymy
where a translation *does* exist in the catalog, and it is far stronger
than TF-IDF on Russian paraphrase. Just don't expect it to conjure
catalog entries that were never there.

---

## 3. Multiple destination files

### The problem

Every destination upload was matched against *every* `MasterProduct` row
ever ingested. Since rows are never deleted, a catalog from an old import
silently contributed candidates to every later run. There was also no way
to express "these five request files belong to the April catalog".

### The model

```
Project  ──pins──▶  master Upload (the catalog)
   │
   └──contains──▶  destination Upload  (Детсад.xlsx)
                   destination Upload  (Школа.xlsx)
                   destination Upload  (Больница.xlsx)
```

Each destination keeps its own column mapping, its own review progress and
its own export. `Upload.project_id` is nullable, so nothing that worked
before breaks and no existing row needs backfilling.

### Endpoints

```
POST   /api/projects                                   create
GET    /api/projects                                   list
GET    /api/projects/{id}                              detail + per-file progress
PUT    /api/projects/{id}/master/{upload_id}           pin the catalog
POST   /api/projects/{id}/destinations/{upload_id}     attach a request file
POST   /api/projects/{id}/reindex                      build index from THIS catalog only
DELETE /api/projects/{id}                              delete grouping (uploads survive)
```

`POST /api/uploads/master` and `/destination` now accept an optional
`project_id`.

### Migration

Your existing `product_matching.db` needs one command, because the app
creates missing tables but never alters existing ones:

```bash
cd backend
python -m scripts.migrate_add_projects --adopt-existing
```

It is additive and idempotent. `--adopt-existing` also creates a project
from your newest master upload and attaches your current uploads to it.

---

## 4. FastAPI, explained

You mentioned the FastAPI parts weren't clear. Here is the whole model,
using code from this project.

### 4.1 The core idea

FastAPI builds your API from **Python type hints**. You write a normal
function with annotated parameters; FastAPI reads those annotations and
automatically handles request parsing, validation, error responses, and
documentation. You never write parsing or validation code by hand.

### 4.2 Routers

A router is a group of endpoints:

```python
router = APIRouter(prefix="/api/projects", tags=["projects"])

@router.get("/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)):
    ...
```

`main.py` mounts it once:

```python
app.include_router(projects.router)
```

The `prefix` is prepended to every path in the router, which is why the
decorator only spells out `/{project_id}` and the real URL is
`/api/projects/{project_id}`. `tags` groups them in the docs page.

### 4.3 Where each parameter comes from

FastAPI decides this from the function signature, by these rules:

```python
@router.post("/uploads/destination")
def upload_destination(
    file: UploadFile,                      # multipart file upload
    sheet_name: str | None = None,         # query string: ?sheet_name=...
    project_id: str | None = None,         # query string: ?project_id=...
    db: Session = Depends(get_db),         # injected dependency, NOT from the request
):
```

- Name appears in the URL path (`/{project_id}`) → **path parameter**
- Type is a Pydantic model → **JSON request body**
- Type is `UploadFile` → **uploaded file**
- Anything else with a default → **query parameter**
- `Depends(...)` → **dependency injection**, never taken from the request

### 4.4 Dependency injection — `Depends`

This is the piece that usually looks like magic. In `database.py`:

```python
def get_db():
    db = SessionLocal()
    try:
        yield db          # hand the session to the endpoint
    finally:
        db.close()        # always runs, after the response is sent
```

When an endpoint declares `db: Session = Depends(get_db)`, FastAPI calls
`get_db()`, passes the yielded session in, and runs the `finally` block
once the response is complete. That is why no endpoint in this codebase
opens or closes a session by hand — and why leaking a connection is not a
bug that can occur here.

The same mechanism is how you would later add authentication: write a
`get_current_user()` dependency, declare it, done.

### 4.5 Pydantic models — the two kinds of "model"

This trips people up, because both are called models:

| | `app/models.py` | `app/schemas.py` |
|---|---|---|
| Library | SQLAlchemy | Pydantic |
| Describes | a database **table** | the **JSON shape** of a request/response |
| Example | `class Project(Base)` | `class ProjectRead(BaseModel)` |

Request validation is automatic:

```python
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
```

`POST /api/projects` with an empty name returns `422` with a precise
message pointing at the field — with zero hand-written checks.

Response serialization is the mirror image:

```python
@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)):
    return db.query(Project).order_by(Project.created_at.desc()).all()
```

You return SQLAlchemy objects; FastAPI converts them using `ProjectRead`.
This works because the schema sets `from_attributes = True`. Crucially,
**any field not declared on the schema is dropped**, so an internal column
can never leak into the API by accident.

### 4.6 Errors

```python
raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
```

Raise it anywhere in the call stack; FastAPI turns it into a JSON error
response. No need to construct or return a Response object.

### 4.7 Background jobs

Matching 1,214 products takes longer than an HTTP request should. The
pattern in `standalone_matching.py` is:

1. `POST .../run/start` starts a thread and immediately returns a `job_id`
2. the thread writes progress into an in-memory registry
3. the frontend polls `GET /api/v1/matching/jobs/{job_id}` for a percentage
4. when status is `done`, the result is on the job

This is fine for one server process. If you ever run multiple workers, the
registry must move to Redis or a database table, because each process has
its own memory.

### 4.8 The free documentation page

Start the backend and open **http://localhost:8000/docs**.

FastAPI generates that from your type hints. Every endpoint is listed with
its parameters, its schemas, and a working "Try it out" button. It is the
fastest way to explore the new project endpoints — no curl needed.

---

## 5. Running it

```bash
# 1. dependencies (LaBSE is now the default embedding model)
cd backend
pip install -r requirements.txt
pip install -r requirements-embeddings.txt

# 2. migrate the existing database
python -m scripts.migrate_add_projects --adopt-existing

# 3. start
python -m uvicorn app.main:app --reload --port 8000
```

First start downloads LaBSE (~1.8 GB), then it runs fully offline. If the
download fails, the app logs a warning and falls back to TF-IDF rather
than refusing to start — matching still works, just weaker on Kazakh.

Then in the UI: create a project, upload `Казниса апрель.xlsx` as its
catalog, upload one or more destination files, and review.

---

## 6. Suggested next step

Auto-accept is deliberately conservative right now (3.8% of rows at
`HIGH=0.88`). That is honest rather than pessimistic — most of your
destination rows genuinely have no exact catalog equivalent.

The way to raise it is the ML layer that already exists but has never had
data: `POST /api/ml/train` needs 500 reviewed decisions. Every confirm and
reject you make in the review UI is stored as training data. Once you pass
500, the classifier learns *your* matching judgment and the confident band
widens on its own. Check progress at `GET /api/ml/training-readiness`.
