"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AutoMatchResult,
  Candidate,
  NextProduct,
  PrioritizeResult,
  Progress,
  ReindexResult,
  ReviewStrategy,
  UploadInfo,
  confirmMatch,
  getNextProduct,
  getProgress,
  listUploads,
  manualSearch,
  rejectMatch,
  reindexSearch,
  runAutoMatch,
  runPrioritize,
  startMatching,
  uploadDestinationFile,
  uploadMasterFile,
} from "@/lib/api";

const LAST_UPLOAD_ID_KEY = "product-matching:last-destination-upload-id";

export default function ReviewTab() {
  const [uploadId, setUploadId] = useState("");
  const [started, setStarted] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [product, setProduct] = useState<NextProduct | null | undefined>(undefined);
  const [selected, setSelected] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualQuery, setManualQuery] = useState("");
  const [manualResults, setManualResults] = useState<Candidate[] | null>(null);
  const [autoMatchResult, setAutoMatchResult] = useState<AutoMatchResult | null>(null);
  const [strategy, setStrategy] = useState<ReviewStrategy>("sequential");
  const [prioritizeResult, setPrioritizeResult] = useState<PrioritizeResult | null>(null);
  const [recentUploads, setRecentUploads] = useState<UploadInfo[] | null>(null);
  const [recentUploadsError, setRecentUploadsError] = useState<string | null>(null);

  const [destUploading, setDestUploading] = useState(false);
  const [destUploadError, setDestUploadError] = useState<string | null>(null);

  const [showMasterSetup, setShowMasterSetup] = useState(false);
  const [masterUploading, setMasterUploading] = useState(false);
  const [masterUploadError, setMasterUploadError] = useState<string | null>(null);
  const [masterUploadResult, setMasterUploadResult] = useState<UploadInfo | null>(null);
  const [reindexResult, setReindexResult] = useState<ReindexResult | null>(null);

  // On first load: restore the last-used upload id from this browser, and
  // fetch the list of previously-uploaded destination files so the user
  // doesn't have to re-upload or hunt for an id every time they reopen the
  // app - the data is already in the database from last time.
  useEffect(() => {
    const saved = window.localStorage.getItem(LAST_UPLOAD_ID_KEY);
    if (saved) {
      setUploadId(saved);
    }
    listUploads("destination")
      .then(setRecentUploads)
      .catch((e) => setRecentUploadsError(e instanceof Error ? e.message : "Could not load recent uploads"));
  }, []);

  const loadNext = useCallback(async (id: string, reviewStrategy: ReviewStrategy) => {
    setLoading(true);
    setError(null);
    try {
      const [p, next] = await Promise.all([getProgress(id), getNextProduct(id, reviewStrategy)]);
      setProgress(p);
      setProduct(next);
      setSelected(null);
      setExpanded(null);
      setManualOpen(false);
      setManualResults(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load next product");
    } finally {
      setLoading(false);
    }
  }, []);

  const handleStart = useCallback(
    async (idOverride?: string) => {
      const id = (idOverride ?? uploadId).trim();
      if (!id) return;
      setLoading(true);
      setError(null);
      try {
        await startMatching(id);
        setUploadId(id);
        window.localStorage.setItem(LAST_UPLOAD_ID_KEY, id);
        setStarted(true);
        await loadNext(id, strategy);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to start matching");
      } finally {
        setLoading(false);
      }
    },
    [uploadId, loadNext, strategy]
  );

  const handleDestinationFile = useCallback(
    async (file: File) => {
      setDestUploading(true);
      setDestUploadError(null);
      try {
        const result = await uploadDestinationFile(file);
        setRecentUploads((prev) => [result.upload, ...(prev ?? [])]);
        await handleStart(result.upload.id);
      } catch (e) {
        setDestUploadError(e instanceof Error ? e.message : "Upload failed");
      } finally {
        setDestUploading(false);
      }
    },
    [handleStart]
  );

  const handleMasterFile = useCallback(async (file: File) => {
    setMasterUploading(true);
    setMasterUploadError(null);
    setMasterUploadResult(null);
    setReindexResult(null);
    try {
      const result = await uploadMasterFile(file);
      setMasterUploadResult(result.upload);
      const stats = await reindexSearch();
      setReindexResult(stats);
    } catch (e) {
      setMasterUploadError(e instanceof Error ? e.message : "Master catalog upload failed");
    } finally {
      setMasterUploading(false);
    }
  }, []);

  const handleAutoMatch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await runAutoMatch(uploadId);
      setAutoMatchResult(result);
      await loadNext(uploadId, strategy);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Auto-match failed");
    } finally {
      setLoading(false);
    }
  }, [uploadId, loadNext, strategy]);

  const handlePrioritize = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await runPrioritize(uploadId);
      setPrioritizeResult(result);
      setStrategy("uncertainty");
      await loadNext(uploadId, "uncertainty");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Prioritization failed");
    } finally {
      setLoading(false);
    }
  }, [uploadId, loadNext]);

  const handleConfirm = useCallback(
    async (masterProductId: string, rank: number) => {
      if (!product) return;
      setLoading(true);
      setError(null);
      try {
        await confirmMatch(uploadId, product.destination_product_id, masterProductId, rank);
        await loadNext(uploadId, strategy);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to confirm match");
      } finally {
        setLoading(false);
      }
    },
    [product, uploadId, loadNext, strategy]
  );

  const handleNone = useCallback(async () => {
    if (!product) return;
    setLoading(true);
    setError(null);
    try {
      await rejectMatch(uploadId, product.destination_product_id);
      await loadNext(uploadId, strategy);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to reject match");
    } finally {
      setLoading(false);
    }
  }, [product, uploadId, loadNext, strategy]);

  const handleManualSearch = useCallback(async () => {
    if (!manualQuery.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const results = await manualSearch(uploadId, manualQuery.trim());
      setManualResults(results);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Manual search failed");
    } finally {
      setLoading(false);
    }
  }, [manualQuery, uploadId]);

  // Keyboard shortcuts: 1/2/3 select, N = none, Enter = confirm selection.
  useEffect(() => {
    if (!started || !product) return;

    function onKeyDown(e: KeyboardEvent) {
      if (["INPUT", "TEXTAREA"].includes((e.target as HTMLElement)?.tagName)) return;

      if (["1", "2", "3"].includes(e.key)) {
        const idx = Number(e.key) - 1;
        if (product && idx < product.candidates.length) {
          setSelected(idx);
        }
      } else if (e.key.toLowerCase() === "n") {
        handleNone();
      } else if (e.key === "Enter") {
        if (selected !== null && product) {
          const candidate = product.candidates[selected];
          handleConfirm(candidate.master_product_id, selected + 1);
        }
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [started, product, selected, handleConfirm, handleNone]);

  const progressPct = progress && progress.total > 0 ? Math.round(((progress.total - progress.pending) / progress.total) * 100) : 0;

  return (
    <>
      {!started && (
        <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <label className="mb-2 block text-sm font-medium text-gray-700">
            Upload a destination file
          </label>
          <input
            type="file"
            accept=".xlsx,.xlsm"
            className="block w-full text-sm text-gray-700 file:mr-3 file:rounded-md file:border-0 file:bg-blue-600 file:px-3 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-blue-700"
            disabled={destUploading || loading}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) handleDestinationFile(file);
            }}
          />
          {destUploading && <p className="mt-2 text-xs text-gray-500">Uploading and ingesting...</p>}
          {destUploadError && <p className="mt-2 text-sm text-red-600">{destUploadError}</p>}
          <p className="mt-1 text-xs text-gray-400">.xlsx / .xlsm - sheet and columns are auto-detected.</p>

          <div className="my-4 flex items-center gap-3 text-xs text-gray-400">
            <div className="h-px flex-1 bg-gray-200" />
            or
            <div className="h-px flex-1 bg-gray-200" />
          </div>

          <label className="mb-2 block text-sm font-medium text-gray-700">
            Destination upload ID
          </label>
          <div className="flex gap-2">
            <input
              className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="e.g. the upload id returned by uploading a destination file"
              value={uploadId}
              onChange={(e) => setUploadId(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleStart()}
            />
            <button
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              onClick={() => handleStart()}
              disabled={loading || !uploadId.trim()}
            >
              Start
            </button>
          </div>
          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

          <div className="mt-5 border-t border-gray-100 pt-4">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
              Or pick a previous upload
            </p>
            {recentUploadsError && (
              <p className="text-xs text-red-600">{recentUploadsError}</p>
            )}
            {recentUploads === null && !recentUploadsError && (
              <p className="text-xs text-gray-400">Loading recent uploads...</p>
            )}
            {recentUploads !== null && recentUploads.length === 0 && (
              <p className="text-xs text-gray-400">
                No destination files uploaded yet - upload one above to get started.
              </p>
            )}
            {recentUploads !== null && recentUploads.length > 0 && (
              <ul className="max-h-48 space-y-1 overflow-y-auto">
                {recentUploads.map((u) => (
                  <li key={u.id}>
                    <button
                      className="w-full rounded-md border border-gray-200 px-3 py-2 text-left text-sm hover:border-blue-300 hover:bg-blue-50 disabled:opacity-50"
                      onClick={() => handleStart(u.id)}
                      disabled={loading}
                    >
                      <span className="font-medium">{u.filename}</span>{" "}
                      <span className="text-xs text-gray-500">
                        ({u.processed_rows} rows, {u.status})
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {!started && (
        <div className="mt-4 rounded-lg border border-gray-200 bg-white shadow-sm">
          <button
            className="flex w-full items-center justify-between px-6 py-3 text-left text-sm font-medium text-gray-700"
            onClick={() => setShowMasterSetup(!showMasterSetup)}
          >
            Catalog setup: upload master catalog
            <span className="text-gray-400">{showMasterSetup ? "−" : "+"}</span>
          </button>
          {showMasterSetup && (
            <div className="border-t border-gray-100 px-6 py-4">
              <p className="mb-3 text-xs text-gray-500">
                One-time setup: upload the master product catalog that destination items are
                matched against. Uploading rebuilds the search index automatically.
              </p>
              <input
                type="file"
                accept=".xlsx,.xlsm"
                className="block w-full text-sm text-gray-700 file:mr-3 file:rounded-md file:border-0 file:bg-gray-800 file:px-3 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-gray-900"
                disabled={masterUploading}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) handleMasterFile(file);
                }}
              />
              {masterUploading && (
                <p className="mt-2 text-xs text-gray-500">Uploading, ingesting, and rebuilding search index...</p>
              )}
              {masterUploadError && <p className="mt-2 text-sm text-red-600">{masterUploadError}</p>}
              {masterUploadResult && (
                <p className="mt-2 text-xs text-gray-600">
                  Ingested {masterUploadResult.filename}: {masterUploadResult.processed_rows} rows
                  processed, {masterUploadResult.skipped_rows} skipped
                  {masterUploadResult.error_report && masterUploadResult.error_report.length > 0
                    ? `, ${masterUploadResult.error_report.length} errors`
                    : ""}
                  .
                </p>
              )}
              {reindexResult && (
                <p className="mt-1 text-xs text-gray-600">
                  Search index rebuilt: {reindexResult.indexed_records} of{" "}
                  {reindexResult.total_records} products indexed (
                  {reindexResult.group_headers_excluded} group headers excluded).
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {started && progress && (
        <div className="mb-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div className="mb-2 flex items-center justify-between text-sm text-gray-600">
            <span>
              Progress: {progress.total - progress.pending} / {progress.total}
            </span>
            <span>{progressPct}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-gray-200">
            <div className="h-full bg-blue-600" style={{ width: `${progressPct}%` }} />
          </div>
          <div className="mt-2 flex items-center justify-between">
            <div className="flex gap-4 text-xs text-gray-500">
              <span>Matched: {progress.matched}</span>
              <span>No match: {progress.no_match}</span>
              <span>Pending: {progress.pending}</span>
              <span>
                Order: {strategy === "uncertainty" ? "most uncertain first" : "sequential"}
              </span>
            </div>
            <div className="flex gap-2">
              <button
                className="rounded-md border border-gray-300 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                onClick={handlePrioritize}
                disabled={loading || progress.pending === 0}
                title="Compute uncertainty for pending items and review the most ambiguous first"
              >
                Prioritize by uncertainty
              </button>
              <button
                className="rounded-md border border-gray-300 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                onClick={handleAutoMatch}
                disabled={loading || progress.pending === 0}
                title="Auto-accept exact matches and any candidate above the high-confidence threshold"
              >
                Run auto-match
              </button>
            </div>
          </div>
          {autoMatchResult && (
            <p className="mt-2 text-xs text-gray-500">
              Auto-match: checked {autoMatchResult.checked}, matched{" "}
              {autoMatchResult.auto_matched} ({autoMatchResult.exact_matches} exact,{" "}
              {autoMatchResult.threshold_matches} high-confidence)
              {autoMatchResult.auto_rejected > 0
                ? `, ${autoMatchResult.auto_rejected} auto-rejected as clearly not a match`
                : ""}
              , {autoMatchResult.still_pending} still need review.
            </p>
          )}
          {prioritizeResult && (
            <p className="mt-1 text-xs text-gray-500">
              Prioritized: {prioritizeResult.computed} of {prioritizeResult.checked} pending items
              scored ({prioritizeResult.insufficient_candidates} had too few candidates to rank).
            </p>
          )}
        </div>
      )}

      {error && started && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {started && product === null && (
        <div className="rounded-lg border border-green-200 bg-green-50 p-6 text-center text-green-800">
          All destination products reviewed.
        </div>
      )}

      {started && product && (
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-100 p-4">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Destination product
            </h2>
            <p className="mt-1 text-lg font-medium">{product.product_name}</p>
            {product.description && (
              // The requested item's own description. Shown in full (not
              // clamped) because it is the thing every candidate below is
              // being compared against - truncating it would defeat the
              // purpose of putting descriptions on screen at all.
              <p className="mt-1 whitespace-pre-line text-sm text-gray-600">
                {product.description}
              </p>
            )}
            <p className="mt-1 text-sm text-gray-500">
              Quantity: {product.quantity ?? "—"} {product.price ? `· Price: ${product.price}` : ""}
            </p>
          </div>

          <div className="p-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
              AI recommendations
            </h3>

            {product.no_reliable_match && (
              <p className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
                No reliable match found. Candidates below are the closest available, but
                confidence is low — consider searching manually.
              </p>
            )}

            {product.candidates.length === 0 && (
              <p className="text-sm text-gray-500">No candidates found at all.</p>
            )}

            <div className="space-y-2">
              {product.candidates.map((c, idx) => (
                <div
                  key={c.master_product_id}
                  className={`cursor-pointer rounded-md border p-3 transition ${
                    selected === idx ? "border-blue-500 bg-blue-50" : "border-gray-200 hover:border-gray-300"
                  }`}
                  onClick={() => setSelected(idx)}
                >
                  <div className="flex items-start gap-3">
                    <input
                      type="radio"
                      checked={selected === idx}
                      onChange={() => setSelected(idx)}
                      className="mt-1"
                    />
                    <div className="flex-1">
                      <p className="text-sm font-medium">
                        {idx + 1}. {c.product_name}
                      </p>

                      {/* Catalog description. This is what makes manual
                          verification possible: catalog names are often
                          generic or truncated, and the deciding detail
                          (dimensions, material, class) lives here.
                          Clamped to 3 lines so three candidates still fit
                          on screen; expands with the "why" toggle. */}
                      {c.description ? (
                        <p
                          className={`mt-1 whitespace-pre-line text-xs text-gray-600 ${
                            expanded === idx ? "" : "line-clamp-3"
                          }`}
                        >
                          {c.description}
                        </p>
                      ) : (
                        <p className="mt-1 text-xs italic text-gray-400">
                          No description in catalog
                        </p>
                      )}

                      <p className="mt-1 text-xs text-gray-500">
                        {c.external_id ? `${c.external_id} · ` : ""}
                        {c.unit ? `Unit: ${c.unit} · ` : ""}
                        {c.price != null ? `Price: ${c.price} · ` : ""}
                        Similarity: {Math.round(c.final_score * 100)}%
                        {" · "}
                        {/* Coverage is the most decisive signal (see
                            search/lexical_overlap.py) - surfacing it next
                            to the score tells the reviewer at a glance
                            whether the request's key words are actually
                            present, rather than a single opaque number. */}
                        Covers {Math.round(c.lexical_overlap_score * 100)}% of terms
                      </p>

                      <button
                        className="mt-1 text-xs text-blue-600 hover:underline"
                        onClick={(e) => {
                          e.stopPropagation();
                          setExpanded(expanded === idx ? null : idx);
                        }}
                      >
                        {expanded === idx ? "Hide details" : "Full description & why"}
                      </button>
                      {expanded === idx && (
                        <ul className="mt-2 list-inside list-disc text-xs text-gray-600">
                          {c.explanation.map((reason, i) => (
                            <li key={i}>{reason}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              <button
                className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                disabled={selected === null || loading}
                onClick={() =>
                  selected !== null &&
                  handleConfirm(product.candidates[selected].master_product_id, selected + 1)
                }
              >
                Confirm selection
              </button>
              <button
                className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                disabled={loading}
                onClick={handleNone}
              >
                None of these
              </button>
              <button
                className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                onClick={() => setManualOpen(!manualOpen)}
              >
                Search manually
              </button>
            </div>

            <p className="mt-2 text-xs text-gray-400">
              Shortcuts: 1/2/3 select · N none · Enter confirm
            </p>

            {manualOpen && (
              <div className="mt-4 rounded-md border border-gray-200 p-3">
                <div className="flex gap-2">
                  <input
                    className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm"
                    placeholder="Search master catalog..."
                    value={manualQuery}
                    onChange={(e) => setManualQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleManualSearch()}
                  />
                  <button
                    className="rounded-md bg-gray-800 px-4 py-2 text-sm font-medium text-white hover:bg-gray-900"
                    onClick={handleManualSearch}
                  >
                    Search
                  </button>
                </div>
                {manualResults && (
                  <ul className="mt-3 space-y-1">
                    {manualResults.map((r) => (
                      <li
                        key={r.master_product_id}
                        className="cursor-pointer rounded-md border border-transparent p-2 text-sm hover:border-gray-200 hover:bg-gray-50"
                        onClick={() => handleConfirm(r.master_product_id, 0)}
                      >
                        <span className="font-medium">{r.product_name}</span>{" "}
                        <span className="text-xs text-gray-400">
                          ({Math.round(r.final_score * 100)}%)
                        </span>
                        {/* Same reasoning as the candidate cards: picking
                            from manual search without seeing the specs is
                            guesswork. Clamped tighter here because this
                            list is longer. */}
                        {r.description && (
                          <span className="mt-0.5 block whitespace-pre-line text-xs text-gray-600 line-clamp-2">
                            {r.description}
                          </span>
                        )}
                        <span className="mt-0.5 block text-xs text-gray-400">
                          {r.external_id ? `${r.external_id} · ` : ""}
                          {r.unit ? `${r.unit} · ` : ""}
                          Covers {Math.round(r.lexical_overlap_score * 100)}% of terms
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
