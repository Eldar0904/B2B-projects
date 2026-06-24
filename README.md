# B2B Fitout Dashboard

Internal dashboard for managing classroom/hall fitout projects (furniture, AV, lighting, sound) — projects, tasks, documents, and an AI-assisted supplier search, backed by Firebase (Auth + Firestore) and deployed via Firebase Hosting.

## Project structure

- `B2B_Fitout_Dashboard_Prototype.html` — master copy of the dashboard. Single-file HTML app, no bundler, uses the Firebase **compat** SDK via `<script src>` tags. Edit this file first, then mirror changes into `firebase-deploy/public/index.html`.
- `supplier-ai.js` — AI Supplier Search component, extracted from the dashboard HTML. Loaded as a classic script, shares global scope with the main inline script (relies on `projects`, `showToast()`, `renderKPIs()`, `supplierRunsCount` from the main script). Currently uses demo/fictitious supplier data (`supplierDB`) — see "Real supplier search" plan below.
- `firebase-deploy/` — Firebase Hosting deploy scaffold.
  - `firebase.json`, `.firebaserc`, `firestore.rules`, `firestore.indexes.json`
  - `public/index.html` + `public/supplier-ai.js` — deployable copies, kept in sync with the root files above.
  - Firebase project ID: `b2b-projects-a7f51`.
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

## Real AI Supplier Search — implementation plan

The Supplier Search UI currently returns fictitious demo data (`supplierDB` in `supplier-ai.js`). To make it return real suppliers, the search has to run server-side via a Firebase Cloud Function (browser JS can't hold API keys safely, and Firebase's free Spark plan blocks Cloud Functions from calling external APIs anyway — only Google's own APIs are allowed on Spark).

**Decided approach:**
- **Search:** Google Custom Search JSON API — 100 free queries/day, then $5/1,000. Chosen over Bright Data's SERP API ($1.50/1,000 pay-as-you-go, no free tier) since this tool's volume is low and Bright Data's CAPTCHA-solving/stealth browsing is overkill for plain supplier lookups.
- **Parsing/ranking:** Claude (Anthropic API) parses the raw search results into the existing supplier-card shape (name, location, price, lead time, MOQ, rating) and ranks them — a few cents per search run.
- **Billing prerequisite:** the Firebase project must be on the **Blaze** (pay-as-you-go) plan to allow Cloud Functions outbound network calls at all. *(Status as of 2026-06-23: user needs to upgrade — not yet done.)* Usage should stay within Firebase's free monthly quota (2M function invocations, etc.) for this tool's volume.

**Steps once Blaze is enabled:**
1. Add a `functions/` directory to `firebase-deploy/` with a callable Cloud Function that calls Google Custom Search, then Claude, to produce ranked supplier results.
2. Update `supplier-ai.js` to call that function instead of reading the local `supplierDB` object, keeping the existing UI/UX (agent-step animation, result cards, shortlist) unchanged.
3. Provision and store as Firebase Function secrets:
   - Google Custom Search API key + Custom Search Engine (CSE) ID
   - Anthropic API key
4. Deploy functions alongside hosting: `firebase deploy --only functions,hosting`.

**Not yet started** — blocked on the Blaze plan upgrade.

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
