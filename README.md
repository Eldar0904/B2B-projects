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
