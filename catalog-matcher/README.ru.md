# Catalog Matcher — MVP (v1, без LLM)

Сопоставляет внутренний список товаров (`Our_Items.xlsx`) с государственным
или поставщическим каталогом (`Government_Catalog.xlsx`), используя только
детерминированное сопоставление. Ранжирование с помощью LLM намеренно
отложено до v2 — в архитектуре уже предусмотрено место под него (см.
раздел «Архитектура» ниже).

## Запуск

### Без терминала (Windows)

Дважды щёлкните в проводнике:

1. **`scripts\start-all.bat`** — запускает backend + frontend и открывает браузер
2. Или по отдельности: `scripts\start-backend.bat` и `scripts\start-frontend.bat`

Окна с серверами **не закрывайте**, пока работаете с приложением.

В **Cursor / VS Code**: `Terminal` → `Run Task` → **Catalog Matcher: Start All**

### Через Docker

```bash
docker compose up --build
```

Или в **Docker Desktop**: Open folder → выбрать проект → Run compose.

- Фронтенд: http://localhost:5173
- Документация backend API: http://localhost:8000/docs

Также можно запустить без Docker — см. раздел «Локальная разработка (без
Docker)» ниже.

## Рабочий процесс

1. Загрузите `Government_Catalog.xlsx` (колонки: Government Code, Product
   Name, Brand, Model, Description, Technical Specifications, Price —
   названия колонок сопоставляются нестрого, небольшие различия допустимы,
   в том числе русскоязычные заголовки).
2. Загрузите `Our_Items.xlsx` (колонки: Item Code, Item Name, Description,
   Quantity).
3. Нажмите «Run matching» («Запустить сопоставление»). Выберите режим:
   - **Быстрый** — код + TF-IDF (~3 мин на 3000 поз.)
   - **Сбалансированный** (рекомендуется) — код + TF-IDF + fuzzy + семантика
   - **Семантический** — как сбалансированный (LLM rerank — в будущем)
   Кнопка «Параметры» открывает top_k, min score и вкл/выкл эмбеддинги.
   Для каждой внутренней позиции:
   - извлекает топ-20 кандидатов по косинусному сходству TF-IDF
   - применяет детерминированные фильтры (усиление за точное/похожее
     совпадение кода, отсечение по минимальному порогу схожести)
   - ранжирует и оставляет топ-3, каждый с оценкой уверенности (0–100%)
     и кратким объяснением
4. Просмотрите таблицу. Лучшее совпадение выбрано по умолчанию; можно
   выбрать другого кандидата переключателем, либо воспользоваться
   «Manual override» («Ручной выбор»), чтобы найти в полном каталоге и
   выбрать вариант вне топ-3.
5. Нажмите «Export» для выгрузки всех результатов, «Export best matches»
   для выгрузки только совпадений с высокой уверенностью (по умолчанию
   ≥80%), либо «Export best matches in batches of 100» для выгрузки лучших
   совпадений частями по 100 строк в одном .zip-архиве.

## Архитектура

```
backend/app/
  matching/
    base.py                 # интерфейсы BaseRetriever / BaseFilter / BaseRanker
    code_retriever.py       # поиск по коду (exact + fuzzy) — до TF-IDF
    fuzzy_retriever.py      # fuzzy по названию (token_set_ratio)
    tfidf_retriever.py      # косинусное сходство TF-IDF
    embedding_retriever.py  # семантика по эмбеддингам (sentence-transformers)
    hybrid_retriever.py     # объединение retriever'ов (RRF)
    merge.py                # Reciprocal Rank Fusion
    matching_config.py      # пресеты fast / balanced / semantic
    deterministic_filter.py # фильтрация по правилам
    heuristic_ranker.py     # ранжирование по оценке
    factory.py               # <- ЕДИНСТВЕННОЕ место, где собираются реализации
  models/db_models.py        # CatalogSource, CatalogProduct, InternalItem, MatchResult
  services/
    excel_import.py          # гибкий разбор заголовков Excel (pandas/openpyxl)
    normalize.py              # очистка текста/кодов/чисел
    export.py                  # генерация файлов экспорта (.xlsx / .zip)
  api/routes.py                # эндпоинты загрузки / сопоставления / просмотра / экспорта
```

Две вещи делают систему расширяемой без изменения самого движка
сопоставления:

- **Несколько каталогов**: `CatalogSource` привязывает каждую строку
  каталога к своему источнику. Добавление нового каталога поставщика —
  это просто ещё одна загрузка с другим `source_name`, без изменения схемы
  или движка.
- **Заменяемые этапы конвейера**: routes.py вызывает только
  `app.matching.factory.build_engine(db, source_id)`. Чтобы позже добавить
  эмбеддинги (pgvector) и переранжирование через LLM:
  1. реализовать `EmbeddingRetriever(BaseRetriever)`, обращающийся к pgvector
  2. реализовать `LLMRanker(BaseRanker)`, вызывающий OpenAI API
  3. изменить `factory.py`, чтобы использовать их вместо версий на
     TF-IDF/эвристике — больше нигде в приложении менять ничего не нужно.

`pgvector` уже подключён в `docker-compose.yml` (сервис `db` использует
образ `pgvector/pgvector:pg16`), поэтому для v2 не потребуется менять
инфраструктуру — только добавить колонку и реализацию retriever'а.

## Локальная разработка (без Docker)

Backend:
```bash
cd backend
python -m venv .venv
```

Windows (PowerShell) — активировать venv и запустить:
```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Если `Activate.ps1` блокируется политикой выполнения, можно без активации:
```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Linux/macOS:
```bash
cd backend
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```
По умолчанию используется локальный файл SQLite (`catalog_matcher.db`).
Для семантики (режим balanced/semantic) установите дополнительно:
`pip install -r requirements-semantic.txt` (скачает модель ~500 МБ при первом запуске).
Чтобы использовать Postgres/pgvector, задайте `DATABASE_URL` и установите
`requirements-postgres.txt`.

Frontend:
```bash
cd frontend
npm install
npm run dev
```

## Известные ограничения v1 (сделано намеренно)

- Сопоставление основано на TF-IDF и правилах, а не на семантических
  эмбеддингах или LLM — хорошо работает для точных/почти точных названий,
  слабее на переформулированных описаниях. Это осознанный объём v1;
  этап LLM добавляется в v2.
- Авторизации нет — добавьте её перед тем, как открывать доступ за пределы
  localhost/внутренней сети.
- Сопоставление заголовков Excel основано на списке алиасов (см.
  `CATALOG_HEADER_ALIASES` / `ITEM_HEADER_ALIASES` в `excel_import.py`);
  для необычных заголовков может понадобиться добавить новый алиас.
