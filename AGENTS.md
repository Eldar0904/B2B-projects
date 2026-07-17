# B2B Projects Rules

These rules apply when Hermes is working in this folder or any of its subfolders.

## Department Context

You are in the B2B department of PINE. Keep B2B and Education work separate unless the user explicitly bridges them.

## Active Products

- B2B Fitout Dashboard — Firebase/Firestore single-file HTML app. Two copies must stay in sync: root `B2B_Fitout_Dashboard_Prototype.html` and `firebase-deploy/public/index.html`.
- Catalog Matcher — Python/FastAPI + React, Docker Compose, v1 is no-LLM by design. v2 slot is already reserved for embeddings + LLM rerank.

## Conventions

- Bash/PowerShell: use git-bash / POSIX syntax in terminal commands.
- Frontend edits: change the root HTML first, then mirror to `firebase-deploy/public/`.
- Backend edits in catalog-matcher: only wire pipeline changes through `factory.py`. New retriever/filter/ranker implementations must go in `matching/`.
- Russian/Kazakh client data is normal. Preserve original text when summarizing, translating only on request.
- Do not invent supplier prices, project budgets, or client contacts. If missing, say so.

## Boundaries

- Do not run shell commands that modify data outside the project folder.
- Do not push to remotes without explicit instruction.
- Do not expose API keys, Firebase config, or credentials in summaries or chat output.
