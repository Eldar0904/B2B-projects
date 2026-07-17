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
3. Copy `config.local.example.js` → `config.local.js` (root and `firebase-deploy/public/`) and paste your keys there. **Do not commit `config.local.js`** — it is in `.gitignore`.
4. **SQL Editor** — run `supabase/storage-setup.sql` (creates bucket + policies).
5. Refresh the dashboard and upload a test file.

Auth stays on **Firebase**; Supabase anon key is used only for file storage (UI requires Firebase login).

## Real AI Supplier Search (Gemini — бесплатно)

Поиск поставщиков работает **напрямую из браузера** через **Gemini + Google Search** (free tier [Google AI Studio](https://aistudio.google.com/apikey)). Не нужны Blaze, Cloud Functions, Claude или Google CSE.

### Setup

1. Откройте [aistudio.google.com/apikey](https://aistudio.google.com/apikey) → **Create API key** (бесплатно).
2. Вставьте ключ в `GEMINI_API_KEY` в `config.local.js` (см. `config.local.example.js`).
3. Обновите дашборд → **ИИ-поиск поставщиков** → разберите позиции → **Найти поставщиков с ИИ**.

### Limits (free tier)

- **Все позиции** из списка обрабатываются (сотни и больше).
- Пакетами по **10 позиций** за один запрос Gemini (~5 с пауза между пакетами).
- Кнопка **«Остановить»** — сохраняются уже найденные результаты.
- При 429 (лимит) — автопауза ~65 с и повтор.

Legacy Cloud Function `searchSuppliers` в `firebase-deploy/functions/` больше не используется UI.

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
