# B2B Fitout Dashboard

Internal dashboard for managing classroom/hall fitout projects (furniture, AV, lighting, sound) — projects, tasks, documents, and an AI-assisted supplier search, backed by Firebase (Auth + Firestore) and deployed via Firebase Hosting.

## Project structure

- `B2B_Fitout_Dashboard_Prototype.html` — master copy of the dashboard. Single-file HTML app, no bundler, uses the Firebase **compat** SDK via `<script src>` tags. Edit this file first, then mirror changes into `firebase-deploy/public/index.html`.
- `supplier-ai.js` — AI Supplier Search component, extracted from the dashboard HTML. Loaded as a classic script, shares global scope with the main inline script (relies on `projects`, `showToast()`, `renderKPIs()`, `supplierRunsCount` from the main script). Currently uses demo/fictitious supplier data (`supplierDB`) — see "Real supplier search" plan below.
- `firebase-deploy/` — Firebase Hosting deploy scaffold.
  - `firebase.json`, `.firebaserc`, `firestore.rules`, `firestore.indexes.json`
  - `public/index.html` + `public/supplier-ai.js` — deployable copies, kept in sync with the root files above.
  - Firebase project ID: `b2b-projects-a7f51`.
- `supabase/storage-setup.sql` — one-time SQL to create the `project-documents` Storage bucket (free tier, no card).
- `Поставщики_по_кабинетам.xlsx`, `Эстрада_поставщики.xlsx` — sourcing/supplier research spreadsheets for specific rooms/halls.

## Two copies, always in sync

Every edit to the dashboard or the supplier-search component must be applied to **both**:
1. the root file (`B2B_Fitout_Dashboard_Prototype.html` / `supplier-ai.js`)
2. the deploy copy (`firebase-deploy/public/index.html` / `firebase-deploy/public/supplier-ai.js`)

## Deploying

```
cd firebase-deploy
firebase deploy --only hosting
# or, if Firestore rules also changed:
firebase deploy --only hosting,firestore:rules
```

## Document uploads (Supabase Storage)

Project files (PDF, Excel, generated acts) are stored in **Supabase Storage** (free tier: **1 GB**, no credit card). Firestore keeps metadata only (`documents` collection).

### Setup

1. Create a project at [supabase.com](https://supabase.com) (free).
2. **Project Settings → API** — copy **Project URL** and **anon public** key.
3. Paste into `SUPABASE_URL` and `SUPABASE_ANON_KEY` in both HTML files (search for `YOUR_SUPABASE_`).
4. **SQL Editor** — run `supabase/storage-setup.sql` (creates bucket + policies).
5. Refresh the dashboard and upload a test file.

Auth stays on **Firebase**; Supabase anon key is used only for file storage (UI requires Firebase login).

## Real AI Supplier Search

The Supplier Search UI calls a **callable Cloud Function** `searchSuppliers` (region `asia-southeast1`):

1. **Google Custom Search JSON API** — web search (~100 free queries/day, then paid).
2. **Claude (Anthropic API)** — parses snippets into ranked supplier cards (name, price, MOQ, lead time, source URL).

If the function is missing, keys are not set, or the project is still on Spark, the UI **falls back to demo data** and shows a toast.

### Prerequisites

- Firebase project on **Blaze** (pay-as-you-go) — required for Cloud Functions outbound HTTP (same as Storage).
- Signed-in user in the dashboard (function rejects unauthenticated calls).

### One-time API setup

1. **Google Custom Search**
   - [Programmable Search Engine](https://programmablesearchengine.google.com/) → create engine → search the entire web.
   - [Google Cloud Console](https://console.cloud.google.com/apis/library/customsearch.googleapis.com?project=b2b-projects-a7f51) → enable **Custom Search API**.
   - Create an API key (Credentials) → copy **API key** and **Search engine ID (cx)**.

2. **Anthropic**
   - [console.anthropic.com](https://console.anthropic.com/) → API key.

### Deploy function + secrets

```powershell
cd firebase-deploy
npm install --prefix functions

npx firebase-tools functions:secrets:set GOOGLE_CSE_API_KEY --project b2b-projects-a7f51
npx firebase-tools functions:secrets:set GOOGLE_CSE_ID --project b2b-projects-a7f51
npx firebase-tools functions:secrets:set ANTHROPIC_API_KEY --project b2b-projects-a7f51

npx firebase-tools deploy --only functions --project b2b-projects-a7f51
```

Also deploy hosting after UI changes:

```powershell
npx firebase-tools deploy --only hosting --project b2b-projects-a7f51
```

### Limits

- Max **8 items** per search run (Google CSE daily quota).
- ~1 CSE query + 1 Claude call per item.

### Files

- `firebase-deploy/functions/index.js` — `searchSuppliers` callable
- `supplier-ai.js` — client UI (live search + demo fallback)

**Previously:** demo-only `supplierDB` in `supplier-ai.js`. **Now:** live search when function + secrets are deployed.

## Дорожная карта ИИ-функций (3 этапа)

Ранжировано по тому, насколько функция зависит от внешней LLM (стоимость, задержка, риск галлюцинаций) против того, что можно получить детерминированной логикой или локальной моделью. Тот же список показан в приложении на вкладке «Обновления».

### Этап 1 — строим первым: чистая логика, без модели

- **Авто-обновление статуса проекта** по активности в задачах — бизнес-правило (все задачи закрыты → проект закрыт).
- **Проверка полноты спецификации** перед подписанием договора — чек-лист: обязательные категории присутствуют или нет.
- **Форматирование документа по стандарту компании** — шаблонизатор (генерация Word/docx), без ИИ.
- **Авто-сравнение смет/комплектаций** и отчёт о расхождениях — сравнение множеств и таблиц.
- **Аналитика по объектам** (средняя стоимость, динамика цен, топ-позиции) — агрегация данных + графики, модель не нужна.
- **Напоминания по дедлайнам** — cron + правила.
- **Рекомендация типовой комплектации кабинета по ФГОС/СанПиН** — после оцифровки стандартов это таблица соответствия «тип кабинета → обязательные позиции», т.е. движок правил, а не ML.

### Этап 2 — строим вторым: классические алгоритмы / локальные модели, без платы за API

- **Нормализация наименований товаров** из разных источников — fuzzy-сравнение строк (rapidfuzz) + словарь синонимов.
- **Парсинг прайс-листов поставщиков** (PDF/Excel) — извлечение таблиц (camelot/tabula, openpyxl) + regex; для структурированных файлов — детерминированно, «грязные» — на ручную проверку.
- **Поиск аналогов** при отсутствии позиции у поставщика — поиск похожих по структурированным характеристикам (категория, размеры, цена), не генеративно.
- **Предсказание риска задержки проекта** — табличное ML (логистическая регрессия / gradient boosting) на собственных исторических данных, работает локально, без API. Нужно достаточно истории.
- **Голосовой ввод на объекте** — локальный Whisper (открытый код), бесплатная и детерминированная транскрипция, без генерации.

### Этап 3 — строим позже: реально нужен LLM (генеративные/открытые задачи)

- **Q&A по базе знаний проекта** (голос или текст) — retrieval + генерация, классический кейс RAG + LLM.
- **ИИ-ассистент закупщика** (вопросы по нормативам) — свободные вопросы-ответы по нормативке.
- **Черновик дефектного акта** по тексту/фото — нужна генерация текста (и зрение — если по фото).
- **Черновики писем/уведомлений поставщикам** — свободное составление текста; можно начать с шаблонов и перейти на LLM для нюансов позже.
- **Распознавание комплектации на фото / фото-контроль поставок** — нужна обученная модель компьютерного зрения, самый трудоёмкий пункт списка.

**Практическая последовательность:** группа «строим первым» закрывает Этапы 1–2 собственной дорожной карты продукта (база цен, генерация спецификаций/актов) — то есть реальную ценность можно дать до подключения любого LLM API. Этап 3+ (фото-контроль, Q&A, риск-предсказание) — там, где уже нужны обученные модели или LLM, и дорожная карта это уже предполагает.
