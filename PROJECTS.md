# Projects — B2B Fitout Dashboard

The **Проекты** section is the hub for each fitout job (school, kindergarten, music school, etc.): one card per site, then a detail page for tasks, documents, procurement, and overview.

Source of truth: `B2B_Fitout_Dashboard_Prototype.html` (mirrored in `firebase-deploy/public/index.html`).

---

## Structure

```
Sidebar → Проекты
│
├── Project cards (list)
│   ├── Name, type icon, status (Активный / Закрыт)
│   ├── Location · client
│   ├── Progress bar (from tasks)
│   ├── Stage stepper П1–П6
│   ├── Stats: % done · tasks · budget · deadline
│   └── Role breakdown (who owns which tasks)
│
└── Project detail (click a card)
    ├── Header: edit · documents · + task
    ├── Stat strip: progress · tasks · docs · budget left · area/rooms
    └── Tabs
        ├── Задачи — kanban
        ├── Документы — registry + upload + generate act
        ├── Закупки / ИИ-поиск — jump to supplier AI for this project
        └── Обзор — passport + budget spend
```

Also tied in:

- **Дэшборд** — active-projects KPI and short list
- **Новый проект** — create / edit modal (header button)

---

## Project passport (fields)

| Field | Purpose |
|--------|---------|
| Name / building | Primary label |
| Type | e.g. Школа, Детский сад (free text) |
| Kind | АКР / ЭП / Без план |
| Stage | П1 → П6 pipeline |
| Location, client | Context for the team |
| Managers | One or more from Team |
| Budget, start/end | Commercial + schedule |
| Area, rooms, floors | Building scale |
| Note | Spec status, contacts, what’s priced |

Data lives in Firestore collection `projects` and is live-synced in the UI.

---

## Stages (П1–П6)

| Stage | Label |
|-------|--------|
| **П1** | Определение типа проекта |
| **П2** | Формирование списка наименований |
| **П3** | Техническое задание и смета |
| **П4** | Доставка и установка |
| **П5** | Финансовое закрытие |
| **П6** | Подписки и постсервис |

Shown as a stepper on project cards, dashboard rows, and the overview tab.

---

## Detail tabs

### Задачи

Kanban columns: **К выполнению** → **В работе** → **Заблокировано** → **Готово**.

- Drag cards between columns (Firestore update)
- Assign team members and due dates
- Progress % on the project is computed from done / total tasks

### Документы

Per-project document registry:

- Upload files (metadata in Firestore, files in Supabase Storage bucket `project-documents`)
- Categories: Спецификация, Контракт, План этажа, Накладная, Разрешение, Фото, Закупка, Акт, Прочее
- Generate act from the project
- Download / delete from the table

### Закупки / ИИ-поиск поставщиков

Shortcut into the supplier-search module for the current project (import positions, optional pull from project documents).

### Обзор

Read-only passport summary + budget utilisation (`spent` / `budget`).

---

## How it helps management

**One place per job** — Each object is a project with its own tasks, docs, and budget, instead of mixing sites in chat or Excel.

**Progress without manual %** — Completion = done tasks / total. Status auto-closes to **Закрыт** when all tasks are «Готово», and reopens to **Активный** if work is added back.

**Workload visibility** — Cards show who has how many tasks done; overdue and open work roll up to dashboard KPIs.

**Documents with the job** — Specs, contracts, and acts stay on the project, ready for supplier import or act generation.

**Procurement handoff** — From the project you open ИИ-поиск with that project selected, or pull an Excel/CSV from its documents.

**Team alignment** — Managers on the project + assignees on tasks keep ownership clear across procurement, site, docs, and install roles.

---

## Typical flow

1. Click **Новый проект** → fill the passport (usually start at stage П1).
2. Open the card → add tasks on the kanban and assign owners.
3. Upload ВОР / specs under **Документы**.
4. Advance stage П2 → П3 as the item list and estimate firm up.
5. Use **Закупки** / supplier AI when sourcing.
6. Project closes automatically when all tasks are **Готово** (or reopens if something returns).

---

## Related data

| Collection / store | Role |
|--------------------|------|
| `projects` (Firestore) | Project passport + status + stage |
| `tasks` (Firestore) | Kanban tasks (`projectId`) |
| `documents` (Firestore) | Doc metadata (`projectId`) |
| Supabase `project-documents` | File blobs |
| `team` (Firestore) | People available as managers / assignees |
