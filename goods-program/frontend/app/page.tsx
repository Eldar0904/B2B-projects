"use client";

import { useState } from "react";
import CatalogTab from "./CatalogTab";

// HANDOFF.md section 14: redesigned down to two tabs. "Upload & Review"
// (the old one-at-a-time keyboard-driven flow, ReviewTab.tsx) and
// "Stats & Training" (StatsTab.tsx) were dropped from navigation - the
// user confirmed the wizard already covers the real workflow and neither
// tab was load-bearing (training in particular never fed live matching -
// see HANDOFF.md's earlier note on that). The component files themselves
// are left on disk, just unimported, rather than deleted outright - this
// folder has no git, so removing code is a one-way door; not referencing
// it costs nothing and is trivially reversible if that changes.
type Tab = "quickmatch" | "catalog";

// The Quick Match Wizard is a plain HTML/JS page under public/matching/,
// embedded in an iframe rather than rewritten as React. It ships its own
// styles and has no build step, so it also works when opened directly.
//
// The API base is handed to it on the query string instead of being
// hardcoded in app.js: that keeps NEXT_PUBLIC_API_BASE_URL the single
// source of truth, so pointing the app at a different backend needs no
// change inside public/.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const WIZARD_SRC = `/matching/index.html?api=${encodeURIComponent(
  `${API_BASE_URL}/api/v1/matching`
)}`;

function WandIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 21 21 6" />
      <path d="M15 6l1.5-3L18 6l3 1.5-3 1.5-1.5 3-1.5-3L12 7.5z" />
      <path d="M4 14l.75-1.5L6.5 12l-1.75-.5L4 10l-.75 1.5L1.5 12l1.75.5z" />
    </svg>
  );
}

function DatabaseIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <ellipse cx="12" cy="6" rx="8" ry="3" />
      <path d="M4 6v6c0 1.66 3.58 3 8 3s8-1.34 8-3V6" />
      <path d="M4 12v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
    </svg>
  );
}

const TABS: { id: Tab; label: string; icon: () => JSX.Element }[] = [
  { id: "quickmatch", label: "Batch match", icon: WandIcon },
  { id: "catalog", label: "Catalog", icon: DatabaseIcon },
];

export default function MatchingPage() {
  const [tab, setTab] = useState<Tab>("quickmatch");

  return (
    <div className="flex min-h-screen bg-gray-50">
      <aside className="flex w-52 flex-shrink-0 flex-col gap-1 border-r border-gray-200 bg-white p-4">
        <div className="mb-4 px-2 text-sm font-semibold text-gray-800">Product Matching</div>
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              className={`flex items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm font-medium transition ${
                active
                  ? "bg-blue-50 text-blue-700"
                  : "text-gray-600 hover:bg-gray-100 hover:text-gray-800"
              }`}
              onClick={() => setTab(t.id)}
            >
              <Icon />
              {t.label}
            </button>
          );
        })}
      </aside>

      <main className="flex-1 min-w-0 p-6">
        {tab === "quickmatch" && (
          <iframe
            src={WIZARD_SRC}
            title="Quick Match Wizard"
            className="h-[calc(100vh-3rem)] w-full rounded-lg border border-gray-200"
          />
        )}
        {tab === "catalog" && <CatalogTab />}
      </main>
    </div>
  );
}
