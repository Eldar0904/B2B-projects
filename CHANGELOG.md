# CHANGELOG

Running log of what changed in the B2B Fitout Dashboard and why. Code-level diffs live in `git log`; this file captures the *decisions* and *context* behind them, which commits alone don't show.

Update this file at the end of each work session — newest entry on top.

---

## 2026-06-25 — Virtual office: presence + activity feed, team chat, roster editor

**Why:** Eldar asked to turn the dashboard into a "virtual office" — team presence, the ability to message the team, and a way to edit the team and see who's assigned where. Scoped via three explicit choices: presence + activity feed layered onto the existing dashboard (not a separate view), one shared team-wide chat channel (not per-project threads), and a Firestore-backed roster editor (project/task assignment stays on the existing dropdowns, no new workload view).

**Added a two-column dashboard layout (`.dash-cols`).** Left column keeps the existing project list and adds a "Лента событий" (activity feed) card below it. Right column adds a "Команда" presence panel (with a "✎ Управление" button opening the roster editor) and a "Командный чат" card.

**Team roster moved from a hardcoded object to Firestore.** `TEAM` is now derived live from the `team` collection (seeded once from `TEAM_SEED` via `seedTeamIfNeeded()`/`meta/teamSeedStatus`, same pattern as the existing changelog/project seeding). The roster editor (`#teamModalOverlay`) lists members with a delete button gated by `confirm()` — the first destructive-delete UI in this app — and a simple add-member form. Every select that lists team members (task assignee, update-entry author, project manager picker, chat sender) refreshes automatically off the `team` onSnapshot via `refreshTeamDependentSelects()`.

**Added one shared team-wide chat** (`messages` collection, ordered by `at` ascending). Any team member can pick their name from `chatSenderSelect` (remembered in `localStorage`) and send a message; no per-project channels.

**Added a lightweight activity feed** (`activity` collection) via `logActivity(text, by)` — fire-and-forget, called on task creation, kanban status changes, project create/edit, and changelog entry creation. Deliberately not called from chat sends, so the feed doesn't duplicate chat content.

**Presence is derived, not tracked.** No separate heartbeat/online collection — `lastSeenFor(name)` checks the most recent chat message or activity entry for that person and treats anyone active in the last 15 minutes (`PRESENCE_WINDOW_MS`) as online.

**Not built:** per-project chat threads, a dedicated workload/assignment view (assignment still happens through the existing task/project manager dropdowns), and a real presence/heartbeat mechanism.

**Mirrored to `firebase-deploy/public/index.html`** via the usual parity pass — CSS block, dashboard markup, team modal markup, TEAM→Firestore migration, all new JS (roster editor, chat, activity log, presence panel), the three new `onSnapshot` listeners + `seedTeamIfNeeded()` in `startRealtimeListeners()`, and all `logActivity()` call sites. Verified line-for-line against the root file.

---

## 2026-06-25 — Fuzzy item matching + Excel/CSV price-list import

**Why:** second AI-roadmap tier (Этап 2 — classical algorithms, no API cost) selected by Eldar: "Нормализация + поиск аналогов товаров" and "Парсинг прайс-листов поставщиков (PDF/Excel)". Both extend the existing deterministic `compare-tools.js` module rather than adding a new one — same no-LLM philosophy.

**Added fuzzy/analog matching to the comparison tool.** `diffLists()` now runs a second pass on items that don't exact-match: a token-sorted, synonym-canonicalized key (`fuzzyKey()`) plus Levenshtein edit distance (`levenshtein()`/`nameSimilarity()`, threshold 0.72) catches the same item written differently — typos, word order, or known phrasing variants (e.g. "доска интерактивная" vs "интерактивная доска"). These show as a new "Вероятно один и тот же товар" section with a similarity %, separate from genuine only-in-A/only-in-B items. Exact-match dedupe (`normalizeItemName`) is untouched, so existing behavior doesn't change — fuzzy matching only kicks in for leftovers.

**Added supplier price-list import (Excel/CSV).** New card in "Проверка и сравнение" with a file input; parses the sheet client-side via SheetJS (loaded from cdnjs), auto-detects наименование/количество/цена columns by header keywords, previews up to 300 rows, and pushes the result straight into the existing Комплектация А/Б or completeness-check textareas — no new data model, reuses the comparison/completeness tools as-is.

**Not built: PDF parsing.** Reliable table extraction from PDF needs server-side tooling (OCR/layout detection) we don't have — flagged in-app, users are told to save as Excel/CSV or paste text manually. Scoped down from the roadmap item, which only classified Excel/structured-file parsing as deterministic; PDF was already flagged in the roadmap as messier.

---

## 2026-06-24 — Kanban board drag-and-drop

**Why:** the kanban board (К выполнению / В работе / Заблокировано / Готово) rendered cards but had no way to move a task between columns — no drag-and-drop, no click handler. Cards just sat there.

**Added:** native HTML5 drag-and-drop on `#kanbanBoard`, event-delegated (listeners attached once, not per render, since `renderKanban()` replaces `innerHTML` on every Firestore snapshot). Dragging a card to another column updates that task's `status` field directly in Firestore (`tasks` collection); the existing `onSnapshot` listener re-renders the board automatically. Visual feedback: dragged card dims (`.tcard.dragging`), target column gets a dashed outline (`.kcol.drag-over`).

**Not changed:** no click-to-edit on cards yet, no touch/mobile drag support (HTML5 drag-and-drop is desktop-only) — flagged for later if needed.

---

## 2026-06-24 — Project type (АКР/ЭП/Без план) + 6-stage pipeline (П1–П6)

**Why:** Eldar shared the PINE B2B process map (Карта процессов, июнь 2026) — 3 phases / 6 processes (П1 Определение типа проекта → П2 Список наименований → П3 ТЗ и смета → П4 Доставка и установка → П5 Финансовое закрытие → П6 Подписки и постсервис). Decided to formalize this as project-level fields instead of leaving it as an external diagram. Category taxonomy review was scoped out — current 3 categories (Мебель/Техника/Дидактика) stay as-is per explicit instruction; Дидактика is the catch-all for everything besides furniture and digital equipment, so it already covers the other categories on the process map.

**Added `kind` field** — values АКР (аудит качества работ) / ЭП (экспериментальный проект) / Без план (разовая поставка без ТЗ), per П1. Optional select in Add/Edit Project modal, shown in the project overview panel.

**Added `stage` field** — П1–П6, defaults to `P1` for new projects and for any existing project missing the field (via `getProjectStage()` helper, same backward-compatible pattern as `getProjectManagers()`). Rendered as a plain segmented progress stepper (`stageStepperHTML()`) wherever progress already shows: dashboard project rows, project cards, and the project detail/overview panel.

**Not yet built (scoped out of this pass, may come later):** document approval status (draft/under review/approved per П3), Finance module (invoicing/АВР/payment status per П5), Subscription module (post-delivery offer→contract→recurring payment per П6) — these need new Firestore collections and new UI views, bigger than a schema addition.

---

## 2026-06-24 — Real project intake + Edit Project feature + task cleanup

**Added the music school fitout project (from `Копия Муз шк.xlsx`) as a real project, not a demo.**
- Did NOT import the 7,697 individual line items, prices, or per-room specs — only a clean project-level summary (per explicit instruction: dashboard should show structure, not the full ВОР).
- Computed and recorded: 232 rooms, 5 floors, 6,608 m² total. 164/232 rooms have full item specs (7,697 lines); 68 rooms have area only, no spec yet.
- Two rooms are fully priced and supplier-confirmed: Эстрада (×6 cabinets, ~29.8M ₸) and the Tezekbayeva hall (81 seats, ~25.6M ₸).
- Key contact recorded: Сурапбергенова Светлана Алмабаевна, 8 705 184 06 03 (music departments).

**Why an "Edit Project" feature was built:** there's no Firebase Admin SDK / service account in this project, so Firestore data can't be written directly from the sandbox once a database is already seeded (the seed script only runs once, on an empty DB). Building Edit Project into the UI was the only way to apply real values to the already-seeded live project — and it doubles as the answer to "what fields should Add Project have."

**New project fields (used for both Add and Edit):** area (m²), room count, floor count, free-text note. Reasoning: budget/dates/manager already existed, but room/floor/area structure is what actually drives fitout planning. The note field is one flexible free-text box instead of many rigid fields, because real intake data (like this xlsx) always has caveats — partial specs, pending quotes, contacts — that don't fit a fixed schema.

**Automation gap found:** `compare-tools.js`'s completeness checker only recognizes 3 categories (Техника/Мебель/Дидактика) via keyword matching, but the real xlsx has 48 category headers (musical instruments, stage lighting, climate control, etc.). Running this project's real room lists through the checker today would falsely flag most items as "missing categories." Not yet fixed — flagged for next session.

**Removed priority chips from tasks.** Decision: priority should be decided by the team, not assigned/encoded by the tool. Removed the priority dropdown from the Add Task modal, the colored priority pill from kanban cards, the `priority` field from new task writes, and from all seed tasks. Kanban board itself (columns, drag-by-status via Firestore, assignee, due date) is unchanged and still fully functional.

**Fixed: nested git repo.** `Fitout Projects/` had its own `.git` folder nested inside the `B2B-projects` repo, which blocked commits ("untracked files present, nothing added"). That sub-repo was fully committed and pushed to its own remote (`github.com/Eldar0904/Fitout-Projects`), so removing the nested `.git` folder was safe — done manually on Eldar's machine (sandbox couldn't delete files inside `.git` due to host-level file locks).

**Fixed: stale `.git/index.lock`.** Left over from an earlier interrupted git operation; was blocking GitHub Desktop even though terminal `git status` looked fine. Removed manually (same host-lock limitation — sandbox couldn't delete it either).

**Note on `firebase.json` location:** lives in `firebase-deploy/`, not the repo root. Run `firebase deploy` from inside `firebase-deploy/`, not from `B2B projects/`.

**Open items carried to next session:**
- Expand `CATEGORY_KEYWORDS` in `compare-tools.js` to cover the real 48-category list from the music school spec.
- 68 rooms in the music school project still have no item-level spec — only area.
