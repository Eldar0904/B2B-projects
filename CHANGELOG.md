# CHANGELOG

Running log of what changed in the B2B Fitout Dashboard and why. Code-level diffs live in `git log`; this file captures the *decisions* and *context* behind them, which commits alone don't show.

Update this file at the end of each work session — newest entry on top.

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
