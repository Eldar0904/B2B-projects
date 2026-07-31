import { useState, useEffect, useCallback, useRef } from "react";
import {
  uploadCatalog, uploadItems, runMatching, cancelMatching, fetchMatchStatus, fetchItems,
  selectMatch, searchCatalogProducts, exportResults, exportResultsBatched,
  fetchMatchCapabilities, fetchCatalogSources, fetchProjects,
} from "./api.js";
import CatalogsPanel from "./CatalogsPanel.jsx";
import ProjectsPanel from "./ProjectsPanel.jsx";

const BEST_MATCH_THRESHOLD = 0.8;
const REVIEW_MIN_THRESHOLD = 0.7; // below this → treat as no useful match
const BATCH_SIZE = 100;

function itemBestScore(item) {
  if (!item.matches?.length) return 0;
  return Math.max(...item.matches.map((m) => m.confidence_score));
}

function isMatchedItem(item) {
  return item.matches.some(
    (m) => m.is_selected && m.confidence_score >= BEST_MATCH_THRESHOLD,
  );
}

function needsReviewItem(item) {
  const best = itemBestScore(item);
  return best >= REVIEW_MIN_THRESHOLD && best < BEST_MATCH_THRESHOLD
    && !isMatchedItem(item);
}

function isPendingItem(item, runActive) {
  return runActive && item.matches.length === 0;
}

function isNoMatchItem(item, runActive = false) {
  if (isPendingItem(item, runActive)) return false;
  return item.matches.length === 0 || itemBestScore(item) < REVIEW_MIN_THRESHOLD;
}

function isMatchRunFinished(st) {
  if (!st || st.status === "idle" || st.status === "complete" || st.status === "cancelled") {
    return true;
  }
  const total = st.items_total || 0;
  const processed = st.items_processed || 0;
  if (total > 0 && processed >= total) return true;
  return false;
}

function tier(score) {
  if (score >= 0.70) return "high";
  if (score >= 0.45) return "medium";
  return "low";
}

function fmtPrice(v) {
  if (v == null) return null;
  return "₸ " + Number(v).toLocaleString("ru-RU");
}

/* ── Manual override search ──────────────────────────────────── */
function ManualOverride({ item, onPicked }) {
  const [open, setOpen] = useState(false);
  const [q, setQ]       = useState("");
  const [results, setResults] = useState([]);

  const search = async (val) => {
    setQ(val);
    if (val.trim().length < 2) { setResults([]); return; }
    const res = await searchCatalogProducts(val);
    setResults(res);
  };

  if (!open) return (
    <div className="override-section">
      <button className="override-trigger" onClick={() => setOpen(true)}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2.5">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        Найти вручную
      </button>
    </div>
  );

  return (
    <div className="override-section">
      <div className="override-search-box">
        <input
          className="override-input"
          placeholder="Введите название или код…"
          value={q}
          autoFocus
          onChange={(e) => search(e.target.value)}
          onBlur={() => setTimeout(() => { setResults([]); setOpen(false); setQ(""); }, 200)}
        />
        {results.length > 0 && (
          <div className="override-results">
            {results.map((r) => (
              <div
                key={r.id}
                className="override-result-item"
                onMouseDown={() => {
                  onPicked(item.id, r.id);
                  setOpen(false);
                  setResults([]);
                  setQ("");
                }}
              >
                <strong>{r.code || "—"}</strong>&nbsp;&nbsp;
                {r.name}
                {r.brand ? <span style={{ color: "#94a3b8" }}> · {r.brand}</span> : null}
                {r.price ? <span style={{ float: "right", fontWeight: 600 }}>{fmtPrice(r.price)}</span> : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Single match option card ─────────────────────────────────── */
function MatchOption({ match, rank, itemId, onSelect }) {
  const t = tier(match.confidence_score);
  const pct = Math.round(match.confidence_score * 100);
  const p = match.catalog_product;

  return (
    <label className={`match-option ${match.is_selected ? "selected" : ""}`}>
      <input
        type="radio"
        name={`item-${itemId}`}
        checked={match.is_selected}
        onChange={() => onSelect(itemId, p.id)}
      />
      <div className="match-rank">Вариант {rank}</div>

      <div className="confidence-row">
        <span className={`conf-badge ${t}`}>{pct}%</span>
        <div className="conf-bar-wrap">
          <div className={`conf-bar ${t}`} style={{ width: `${pct}%` }} />
        </div>
      </div>

      {p.source_name && (
        <div className="source-badge" title={p.source_name}>{p.source_name}</div>
      )}
      {p.code && <div className="match-code">{p.code}</div>}
      <div className="match-name">{p.name}</div>

      <div className="match-detail">
        <span>{p.brand || ""}{p.model ? ` ${p.model}` : ""}</span>
        {p.price && <span className="match-price">{fmtPrice(p.price)}</span>}
      </div>

      {match.is_manual_override && (
        <div style={{ fontSize: 10.5, color: "#7c3aed", fontWeight: 600, marginTop: 2 }}>
          ✦ Выбрано вручную
        </div>
      )}
      {!match.is_selected && rank === 1 && pct < REVIEW_MIN_THRESHOLD * 100 && (
        <div style={{ fontSize: 10.5, color: "#b45309", fontWeight: 600, marginTop: 2 }}>
          ⚠ Низкая уверенность — не рекомендуется
        </div>
      )}
    </label>
  );
}

/* ── Item card ────────────────────────────────────────────────── */
function ItemCard({ item, index, onSelect }) {
  const matches = [...item.matches].sort((a, b) => a.rank - b.rank);
  const placeholders = Math.max(0, 3 - matches.length);

  return (
    <div className="item-card">
      {/* left: source item */}
      <div className="item-source">
        <div className="item-index">#{index + 1}</div>
        <div className="item-name">{item.item_name}</div>
        <div className="item-meta">
          {item.item_code && <span className="item-tag">{item.item_code}</span>}
          {item.quantity != null && (
            <span className="item-tag">Кол-во: {item.quantity}</span>
          )}
          {item.category_name && (
            <span className="item-tag item-tag-category" title={item.category_code || ""}>
              {item.category_name}
            </span>
          )}
        </div>
      </div>

      {/* right: match options */}
      <div className="item-matches">
        {matches.length === 0 ? (
          <p className="no-matches-msg">Совпадений не найдено</p>
        ) : (
          <div className="matches-row">
            {matches.map((m, i) => (
              <MatchOption
                key={m.id}
                match={m}
                rank={i + 1}
                itemId={item.id}
                onSelect={onSelect}
              />
            ))}
            {Array.from({ length: placeholders }).map((_, i) => (
              <div key={`ph-${i}`} className="no-match-slot">—</div>
            ))}
          </div>
        )}
        <ManualOverride item={item} onPicked={onSelect} />
      </div>
    </div>
  );
}

/* ── File button ─────────────────────────────────────────────── */
function FileButton({ label, file, onChange }) {
  return (
    <label className={`file-label ${file ? "has-file" : ""}`}>
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth="2">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
      </svg>
      {file ? file.name : label}
      <input type="file" accept=".xlsx" onChange={(e) => onChange(e.target.files[0])} />
    </label>
  );
}

/* ── Main app ─────────────────────────────────────────────────── */
export default function App() {
  const [view, setView] = useState("match"); // match | catalogs | projects
  const [catalogFile, setCatalogFile] = useState(null);
  const [itemsFile,   setItemsFile]   = useState(null);
  const [items,       setItems]       = useState([]);
  const [status,      setStatus]      = useState(null);
  const [loading,     setLoading]     = useState(false);
  const [busyAction,  setBusyAction]  = useState(null); // catalog | items | match | export
  const [filter,      setFilter]      = useState("all");
  const [matchMode,   setMatchMode]   = useState("balanced");
  const [capabilities, setCapabilities] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [sources, setSources] = useState([]);
  const [selectedSourceIds, setSelectedSourceIds] = useState([]);
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(null);
  const [sourcesCollapsed, setSourcesCollapsed] = useState(false);
  const [advOpts, setAdvOpts] = useState({
    topK: "",
    minScore: "",
    useEmbeddings: null,
    useCategoryFilter: true,
    inferCategory: true,
  });
  const [catalogStats, setCatalogStats] = useState({ productCount: 0, sourceName: "government" });
  const [lastCatalogImport, setLastCatalogImport] = useState(() => {
    try {
      const raw = sessionStorage.getItem("catalogMatcher.lastCatalogImport");
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  });
  const [lastItemsImport, setLastItemsImport] = useState(() => {
    try {
      const raw = sessionStorage.getItem("catalogMatcher.lastItemsImport");
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  });
  const [matchProgress, setMatchProgress] = useState(null); // { processed, total, runId }
  const [matchingActive, setMatchingActive] = useState(false);
  const pollRef = useRef(null);
  const completedRunRef = useRef(null);
  const startPollingRef = useRef(null);

  const toast = (type, text) => {
    setStatus({ type, text });
    setTimeout(() => setStatus(null), 5000);
  };

  const load = useCallback(async () => {
    try {
      setItems(await fetchItems(projectId));
      const srcList = await fetchCatalogSources();
      setSources(srcList);
      setSelectedSourceIds((prev) => {
        if (prev.length) {
          const enabled = new Set(srcList.filter((s) => s.is_enabled).map((s) => s.id));
          const kept = prev.filter((id) => enabled.has(id));
          return kept.length ? kept : srcList.filter((s) => s.is_enabled).map((s) => s.id);
        }
        return srcList.filter((s) => s.is_enabled).map((s) => s.id);
      });
      const gov = srcList.find((s) => s.name === "government") || srcList[0];
      const totalProducts = srcList.reduce((acc, s) => acc + (s.product_count || 0), 0);
      if (gov) {
        setCatalogStats({ productCount: totalProducts || gov.product_count, sourceName: gov.name });
      } else {
        setCatalogStats({ productCount: totalProducts, sourceName: "government" });
      }
      try {
        setProjects(await fetchProjects());
      } catch {
        setProjects([]);
      }
    }
    catch (e) { toast("error", e.message); }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const pollMatchProgress = useCallback(async () => {
    const st = await fetchMatchStatus();
    if (!isMatchRunFinished(st)) {
      setMatchProgress({
        processed: st.items_processed || 0,
        total: st.items_total || 0,
        runId: st.run_id,
      });
      setMatchingActive(true);
      setItems(await fetchItems());
      return st;
    }
    setMatchProgress(null);
    setMatchingActive(false);
    setItems(await fetchItems());
    return st;
  }, []);

  const startPolling = useCallback((onComplete) => {
    stopPolling();
    const tick = async () => {
      try {
        const st = await pollMatchProgress();
        if (isMatchRunFinished(st)) {
          stopPolling();
          if (onComplete && completedRunRef.current !== st.run_id) {
            completedRunRef.current = st.run_id;
            onComplete(st);
          }
        }
      } catch (e) {
        stopPolling();
        setMatchingActive(false);
        setMatchProgress(null);
        toast("error", e.message);
      }
    };
    tick();
    pollRef.current = setInterval(tick, 2500);
  }, [pollMatchProgress, stopPolling]);

  startPollingRef.current = startPolling;

  const syncMatchingState = useCallback(async ({ notify = false } = {}) => {
    try {
      const st = await fetchMatchStatus();
      if (!isMatchRunFinished(st)) {
        setMatchProgress({
          processed: st.items_processed || 0,
          total: st.items_total || 0,
          runId: st.run_id,
        });
        setMatchingActive(true);
        setItems(await fetchItems());
        startPollingRef.current?.((done) => {
          toast("success", `Подбор завершён — ${done.items_processed} позиций`);
        });
        if (notify) {
          toast(
            "success",
            `Сопоставление уже идёт — ${st.items_processed} / ${st.items_total}`,
          );
        }
        return true;
      }
      setMatchingActive(false);
      setMatchProgress(null);
      stopPolling();
      return false;
    } catch {
      return false;
    }
  }, [stopPolling]);

  useEffect(() => {
    syncMatchingState();
    return () => stopPolling();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount
  }, []);

  useEffect(() => {
    if (filter === "pending") setFilter("all");
  }, [filter]);

  useEffect(() => {
    fetchMatchCapabilities()
      .then((c) => {
        setCapabilities(c);
        setMatchMode(c.default_mode || "balanced");
      })
      .catch(() => {});
  }, []);

  const wrap = async (action, fn) => {
    setLoading(true);
    setBusyAction(action);
    try { await fn(); }
    catch (e) { toast("error", e.message); }
    finally {
      setLoading(false);
      setBusyAction(null);
    }
  };

  const handleUploadCatalog = () => wrap("catalog", async () => {
    if (!catalogFile) return;
    const r = await uploadCatalog(catalogFile, {
      sourceName: "government",
      importMode: "upsert",
      computeEmbeddings: false,
      kind: "government",
    });
    const info = { rows: r.rows_imported, name: catalogFile.name };
    setLastCatalogImport(info);
    sessionStorage.setItem("catalogMatcher.lastCatalogImport", JSON.stringify(info));
    toast(
      "success",
      `Каталог обновлён (upsert) — ${r.rows_imported} строк` +
        (r.inserted != null ? ` (+${r.inserted} / ~${r.updated})` : "") +
        ". Нажмите «Запустить», когда будете готовы.",
    );
    await load();
  });

  const handleUploadItems = () => wrap("items", async () => {
    if (!itemsFile) return;
    const r = await uploadItems(itemsFile, projectId);
    const info = { rows: r.rows_imported, name: itemsFile.name };
    setLastItemsImport(info);
    sessionStorage.setItem("catalogMatcher.lastItemsImport", JSON.stringify(info));
    toast("success", `Позиции загружены — ${r.rows_imported} строк. Сопоставление не запущено.`);
    await load();
  });

  const handleCancelMatching = async () => {
    try {
      const r = await cancelMatching();
      if (r.status === "idle") {
        toast("error", "Нет активного сопоставления");
        return;
      }
      stopPolling();
      setMatchingActive(false);
      setMatchProgress(null);
      await load();
      toast("success", `Остановлено на ${r.items_processed} из ${r.items_total} позиций`);
    } catch (e) {
      toast("error", e.message);
    }
  };

  const handleRunMatching = async () => {
    if (matchingActive) {
      toast("error", "Сопоставление уже выполняется");
      return;
    }
    if (!catalogReady && catalogStats.productCount === 0) {
      toast("error", "Сначала загрузите каталог");
      return;
    }
    if (!selectedSourceIds.length) {
      toast("error", "Выберите хотя бы один каталог для подбора");
      return;
    }
    if (!itemsReady && items.length === 0) {
      toast("error", "Сначала загрузите список позиций (шаг 2)");
      return;
    }
    completedRunRef.current = null;
    setLoading(true);
    setMatchingActive(true);
    try {
      const opts = {
        matchingMode: matchMode,
        sourceIds: selectedSourceIds.length ? selectedSourceIds : null,
        projectId: projectId || null,
      };
      if (advOpts.topK !== "") opts.topKCandidates = Number(advOpts.topK);
      if (advOpts.minScore !== "") opts.minSimilarityScore = Number(advOpts.minScore);
      if (advOpts.useEmbeddings !== null) opts.useEmbeddings = advOpts.useEmbeddings;
      opts.useCategoryFilter = advOpts.useCategoryFilter;
      opts.inferCategoryIfMissing = advOpts.inferCategory;

      const r = await runMatching(opts);
      setMatchProgress({
        processed: 0,
        total: r.items_total || total,
        runId: r.run_id,
      });
      const modeLabel = (capabilities?.modes || []).find((m) => m.id === matchMode)?.label || matchMode;
      toast(
        "success",
        matchMode === "semantic"
          ? `Сопоставление запущено (${modeLabel}). Семантика: ~15–30 мин на 1200 позиций — прогресс обновляется ниже.`
          : `Сопоставление запущено (${modeLabel})`,
      );
      startPolling((done) => {
        toast("success", `Подбор завершён — ${done.items_processed} позиций`);
      });
    } catch (e) {
      if (e.status === 409) {
        await syncMatchingState({ notify: true });
      } else {
        toast("error", e.message);
        setMatchingActive(false);
        setMatchProgress(null);
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSelect = async (itemId, productId) => {
    try {
      await selectMatch(itemId, productId);
      await load();
    } catch (e) { toast("error", e.message); }
  };

  const handleExport = (minConf = null) => wrap("export", async () => {
    await exportResults(minConf);
    toast("success", "Файл экспортирован");
  });

  const handleExportBatched = () => wrap("export", async () => {
    await exportResultsBatched(BEST_MATCH_THRESHOLD, BATCH_SIZE);
    toast("success", "Архив батчей экспортирован");
  });

  // ── Stats ──────────────────────────────────────────────────────
  const dbItemsCount   = items.length;
  const dbCatalogCount = catalogStats.productCount;
  const sessionCatalogCount = lastCatalogImport?.rows ?? null;
  const sessionItemsCount   = lastItemsImport?.rows ?? null;
  const catalogReady = sessionCatalogCount != null && sessionCatalogCount > 0;
  const itemsReady   = sessionItemsCount != null && sessionItemsCount > 0;
  const hasAnyMatches = items.some((i) => i.matches.length > 0);
  const canViewResults = itemsReady || matchingActive || hasAnyMatches;
  const total        = dbItemsCount;
  const hasData      = catalogReady || itemsReady || dbCatalogCount > 0 || dbItemsCount > 0;
  const matched   = items.filter(isMatchedItem).length;
  const needsWork = items.filter(needsReviewItem).length;
  const noMatch   = items.filter((i) => isNoMatchItem(i, matchingActive)).length;

  const filterTabs = [
    { id: "all",        label: `Все (${total})` },
    { id: "matched",    label: `Подобраны (${matched})` },
    { id: "needs-work", label: `Проверить (${needsWork})` },
    { id: "no-match",   label: `Без совпадений (${noMatch})` },
  ];

  // ── Filtered items ─────────────────────────────────────────────
  const visible = items.filter(i => {
    if (filter === "matched") return isMatchedItem(i);
    if (filter === "needs-work") return needsReviewItem(i);
    if (filter === "no-match") return isNoMatchItem(i, matchingActive);
    return true;
  });

  return (
    <div className="page">

      {/* ── Header ── */}
      <header className="page-header">
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <h1>Catalog Matcher</h1>
          <nav className="header-nav">
            {[
              ["match", "Подбор"],
              ["catalogs", "Каталоги"],
              ["projects", "Проекты"],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={`header-nav-btn ${view === id ? "active" : ""}`}
                onClick={() => setView(id)}
              >
                {label}
              </button>
            ))}
          </nav>
          <span className="sub">мульти-каталог · upsert · версии</span>
        </div>
        <div className="header-right">
          {(loading || matchingActive) && <div className="spinner" />}
          {matchingActive && matchProgress && matchProgress.total > 0 && (
            <span className="match-progress-label">
              {matchProgress.processed} / {matchProgress.total}
            </span>
          )}
          <span style={{ fontSize: 12, color: "rgba(255,255,255,.5)" }}>
            каталог: {sessionCatalogCount ?? "—"} · позиции: {sessionItemsCount ?? "—"}
          </span>
        </div>
      </header>

      <div className="main">

        {view === "catalogs" && (
          <CatalogsPanel onToast={(type, text) => toast(type, text)} />
        )}
        {view === "projects" && (
          <ProjectsPanel onToast={(type, text) => toast(type, text)} />
        )}

        {view === "match" && (<>
        {/* ── Workflow steps ── */}
        <div className="workflow-card">

          <div className="workflow-step">
            <div className="step-body">
              <div className="step-label">
                <span className="step-num">1</span>Каталог (.xlsx)
                {catalogReady && (
                  <span className="step-badge step-badge-catalog">{sessionCatalogCount} загружено</span>
                )}
              </div>
              <div className="step-controls">
                <FileButton label="Выбрать файл" file={catalogFile} onChange={setCatalogFile} />
                <button className="btn btn-ghost btn-sm"
                  onClick={handleUploadCatalog} disabled={!catalogFile || loading}>
                  {busyAction === "catalog"
                    ? <><div className="spinner" /> Загрузка…</>
                    : "Загрузить"}
                </button>
              </div>
            </div>
          </div>

          <div className="step-divider" />

          <div className="workflow-step">
            <div className="step-body">
              <div className="step-label">
                <span className="step-num">2</span>Наши позиции (.xlsx)
                {itemsReady && (
                  <span className="step-badge step-badge-items">{sessionItemsCount} загружено</span>
                )}
              </div>
              <div className="step-controls">
                <FileButton label="Выбрать файл" file={itemsFile} onChange={setItemsFile} />
                <button className="btn btn-ghost btn-sm"
                  onClick={handleUploadItems} disabled={!itemsFile || loading}>
                  {busyAction === "items"
                    ? <><div className="spinner" /> Загрузка…</>
                    : "Загрузить"}
                </button>
              </div>
            </div>
          </div>

          <div className="step-divider" />

          <div className="workflow-step">
            <div className="step-body">
              <div className="step-label">
                <span className="step-num">3</span>Запустить подбор
              </div>
              <div className="source-pick">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setSourcesCollapsed((v) => !v)}
                >
                  {sourcesCollapsed ? "▸" : "▾"} Каталоги для подбора ({selectedSourceIds.length})
                </button>
                {projects.length > 0 && (
                  <select
                    className="mode-select"
                    value={projectId || ""}
                    onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)}
                    title="Проект (опционально)"
                  >
                    <option value="">Без проекта</option>
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                )}
                {!sourcesCollapsed && (
                  <div className="source-check-list">
                    {sources.filter((s) => !s.is_archived).map((s) => (
                      <label key={s.id} className={`chk-inline source-check ${!s.is_enabled ? "dim" : ""}`}>
                        <input
                          type="checkbox"
                          disabled={!s.is_enabled}
                          checked={selectedSourceIds.includes(s.id)}
                          onChange={(e) => {
                            setSelectedSourceIds((prev) => (
                              e.target.checked
                                ? [...prev, s.id]
                                : prev.filter((id) => id !== s.id)
                            ));
                          }}
                        />
                        <span className={`source-kind ${s.kind}`}>
                          {s.kind === "government" ? "Гос" : "Пост."}
                        </span>
                        {s.name}
                        <span className="source-row-meta">({s.product_count})</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
              <div className="step-controls match-controls">
                <select
                  className="mode-select"
                  value={matchMode}
                  onChange={(e) => setMatchMode(e.target.value)}
                  disabled={loading}
                  title="Режим сопоставления"
                >
                  {(capabilities?.modes || [
                    { id: "fast", label: "Быстрый" },
                    { id: "balanced", label: "Сбалансированный" },
                    { id: "semantic", label: "Семантический" },
                  ]).map((m) => (
                    <option key={m.id} value={m.id}>{m.label}</option>
                  ))}
                </select>
                <button className="btn btn-primary"
                  onClick={handleRunMatching}
                  disabled={loading || (!itemsReady && items.length === 0) || matchingActive || !selectedSourceIds.length}>
                  {matchingActive
                    ? <><div className="spinner" style={{ borderTopColor: "#fff" }} /> {matchProgress?.total
                      ? `Обработка ${matchProgress.processed}/${matchProgress.total}…`
                      : "Сопоставление…"}</>
                    : "▶ Запустить"}
                </button>
                {matchingActive && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={handleCancelMatching}
                    disabled={loading}
                  >
                    Остановить
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setShowAdvanced((v) => !v)}
                >
                  {showAdvanced ? "Скрыть" : "Параметры"}
                </button>
              </div>
              {showAdvanced && (
                <div className="advanced-panel">
                  <label>
                    top_k
                    <input
                      type="number"
                      min="5"
                      max="200"
                      placeholder="авто"
                      value={advOpts.topK}
                      onChange={(e) => setAdvOpts({ ...advOpts, topK: e.target.value })}
                    />
                  </label>
                  <label>
                    min score
                    <input
                      type="number"
                      min="0"
                      max="1"
                      step="0.05"
                      placeholder="0.35"
                      value={advOpts.minScore}
                      onChange={(e) => setAdvOpts({ ...advOpts, minScore: e.target.value })}
                    />
                  </label>
                  <label>
                    эмбеддинги
                    <select
                      value={advOpts.useEmbeddings === null ? "" : advOpts.useEmbeddings ? "1" : "0"}
                      onChange={(e) => {
                        const v = e.target.value;
                        setAdvOpts({
                          ...advOpts,
                          useEmbeddings: v === "" ? null : v === "1",
                        });
                      }}
                    >
                      <option value="">из режима</option>
                      <option value="1">вкл</option>
                      <option value="0">выкл</option>
                    </select>
                  </label>
                  <label className="adv-check">
                    <input
                      type="checkbox"
                      checked={advOpts.useCategoryFilter}
                      onChange={(e) => setAdvOpts({ ...advOpts, useCategoryFilter: e.target.checked })}
                    />
                    фильтр по категории
                  </label>
                  <label className="adv-check">
                    <input
                      type="checkbox"
                      checked={advOpts.inferCategory}
                      onChange={(e) => setAdvOpts({ ...advOpts, inferCategory: e.target.checked })}
                    />
                    угадывать категорию
                  </label>
                  {capabilities && !capabilities.embeddings_available && (
                    <span className="adv-hint">Семантика: установите sentence-transformers на backend</span>
                  )}
                </div>
              )}
            </div>
          </div>

          <div className="step-divider" />

          <div className="workflow-step">
            <div className="step-body">
              <div className="step-label">
                <span className="step-num">4</span>Экспорт
              </div>
              <div className="step-controls export-btns">
                <button className="btn btn-ghost btn-sm"
                  onClick={() => handleExport()} disabled={loading || !canViewResults}>
                  Все позиции
                </button>
                <button className="btn btn-ghost btn-sm"
                  onClick={() => handleExport(BEST_MATCH_THRESHOLD)} disabled={loading || !canViewResults}>
                  ≥{BEST_MATCH_THRESHOLD * 100}%
                </button>
                <button className="btn btn-green btn-sm"
                  onClick={handleExportBatched} disabled={loading || !canViewResults}>
                  Батчи .zip
                </button>
              </div>
            </div>
          </div>

        </div>

        {/* ── Status bar ── */}
        {status && (
          <div className={`status-bar ${status.type}`}>
            {status.type === "success" ? "✓" : "✕"}&nbsp;{status.text}
          </div>
        )}

        {matchingActive && matchProgress && matchProgress.total > 0 && (
          <div className="match-progress-bar">
            <div className="match-progress-track">
              <div
                className="match-progress-fill"
                style={{
                  width: `${Math.min(100, Math.round((matchProgress.processed / matchProgress.total) * 100))}%`,
                }}
              />
            </div>
            <div className="match-progress-status">
              Обработано {matchProgress.processed}/{matchProgress.total}
              <span className="match-progress-sep">·</span>
              подобрано {matched}
              <span className="match-progress-sep">·</span>
              проверить {needsWork}
              <span className="match-progress-sep">·</span>
              слабые {noMatch}
            </div>
          </div>
        )}

        {/* ── Loaded data summary ── */}
        {hasData && (
          <div className="data-summary">
            <div className="data-summary-title">Загружено в базу</div>
            <div className="data-summary-row">
              <div className={`data-card ${catalogReady ? "ok" : "empty"}`}>
                <div className="data-card-label">Каталог (шаг 1)</div>
                {catalogReady && (
                  <div className="data-card-value">{sessionCatalogCount}</div>
                )}
                <div className="data-card-hint">
                  {lastCatalogImport
                    ? `${lastCatalogImport.name}: ${lastCatalogImport.rows} строк`
                    : "загрузите файл"}
                </div>
              </div>
              <div className={`data-card ${itemsReady ? "ok" : "empty"}`}>
                <div className="data-card-label">Наши позиции (шаг 2)</div>
                {itemsReady && (
                  <div className="data-card-value">{sessionItemsCount}</div>
                )}
                <div className="data-card-hint">
                  {lastItemsImport
                    ? `${lastItemsImport.name}: ${lastItemsImport.rows} строк`
                    : "загрузите файл"}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── Filter tabs ── */}
        {canViewResults && total > 0 && (
          <div className="filter-bar">
            {filterTabs.map(f => (
              <button key={f.id}
                className={`filter-tab ${filter === f.id ? "active" : ""}`}
                onClick={() => setFilter(f.id)}>
                {f.label}
              </button>
            ))}
          </div>
        )}

        {/* ── Items list ── */}
        {!canViewResults ? (
          <div className="empty-state">
            {catalogReady || dbCatalogCount > 0 ? (
              <p>Каталог готов. Загрузите список позиций (шаг 2).</p>
            ) : (
              <>
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none"
                  stroke="#94a3b8" strokeWidth="1.5">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                  <line x1="16" y1="13" x2="8" y2="13"/>
                  <line x1="16" y1="17" x2="8" y2="17"/>
                </svg>
                <p>Загрузите каталог и список позиций, затем запустите подбор</p>
              </>
            )}
          </div>
        ) : visible.length === 0 ? (
          <div className="empty-state">
            <p>Нет позиций по выбранному фильтру</p>
          </div>
        ) : (
          visible.map((item) => (
            <ItemCard
              key={item.id}
              item={item}
              index={items.indexOf(item)}
              onSelect={handleSelect}
            />
          ))
        )}
        </>)}

        {status && view !== "match" && (
          <div className={`status-bar ${status.type}`}>
            {status.type === "success" ? "✓" : "✕"}&nbsp;{status.text}
          </div>
        )}

      </div>
    </div>
  );
}
