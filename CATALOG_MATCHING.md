# Подбор по каталогу = Catalog Matcher (GoodsProgram)

GoodsProgram перенесён в этот репозиторий как **`catalog-matcher/`** и встроен во вкладку сайдбара **Подбор по каталогу**.

Исходник: `C:\Users\Eldar\Documents\GitHub\GoodsProgram`  
Копия в дашборде: `C:\Users\Eldar\Documents\GitHub\B2B-projects\catalog-matcher`

---

## Быстрый старт

1. Docker Desktop должен быть запущен.
2. Из корня B2B-projects:
   ```
   start-catalog-matcher.bat
   ```
   (внутри: `docker compose up --build` → Postgres :5433, Qdrant :6333, API :8000, UI :3000)
3. В другом окне:
   ```
   start-dashboard.bat
   ```
4. Открыть дашборд → **Подбор по каталогу** — UI Matcher внутри iframe.

Без Docker остаётся **локальный fallback** (два Excel в браузере) — сверните блок внизу вкладки.

---

## Порты

| Сервис | Порт |
|--------|------|
| Matcher UI (Next.js) | `3000` |
| Matcher API (FastAPI) | `8000` |
| Postgres (Docker) | `5433` → контейнер 5432 |
| Qdrant | `6333` |
| B2B Dashboard (static) | `5500` |

---

## Что изменено в дашборде

| Файл | Изменение |
|------|-----------|
| `B2B_Fitout_Dashboard_Prototype.html` | iframe на `:3000` + статус API + fallback |
| `compare-tools.js` | мост под GoodsProgram: `GET /health`, `GET /api/projects`, UI `:3000` |
| `firebase-deploy/public/*` | те же копии |
| `catalog-matcher/backend/app/main.py` | CORS для `:5500` (дашборд) |
| `start-catalog-matcher.bat` | запуск Docker из корня |

Старый мост (`/catalog-sources`, `/match/run`, UI `:5173`) **удалён** — он не совпадал с GoodsProgram.

---

## Документация Matcher

Внутри `catalog-matcher/`: `README.md`, `HANDOFF.md`, `ARCHITECTURE.md`, `DATABASE.md`.

API docs: http://localhost:8000/docs
