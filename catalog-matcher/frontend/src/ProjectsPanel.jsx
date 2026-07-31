import { useEffect, useState } from "react";
import {
  createProject,
  fetchCatalogSources,
  fetchProjects,
  putProjectCatalogLinks,
  uploadItems,
} from "./api.js";

export default function ProjectsPanel({ onToast }) {
  const toast = onToast || (() => {});
  const [projects, setProjects] = useState([]);
  const [sources, setSources] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [name, setName] = useState("");
  const [itemsFile, setItemsFile] = useState(null);
  const [busy, setBusy] = useState(false);

  const selected = projects.find((p) => p.id === selectedId) || null;

  const reload = async () => {
    const [p, s] = await Promise.all([fetchProjects(), fetchCatalogSources()]);
    setProjects(p);
    setSources(s);
    if (!selectedId && p.length) setSelectedId(p[0].id);
  };

  useEffect(() => {
    reload().catch((e) => toast("error", e.message));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const linkMap = {};
  if (selected) {
    for (const lnk of selected.catalog_links || []) {
      linkMap[lnk.source_id] = lnk;
    }
  }

  return (
    <div className="catalogs-panel">
      <div className="catalogs-sidebar">
        <div className="catalogs-sidebar-head">
          <strong>Проекты</strong>
          <button
            className="btn btn-ghost btn-sm"
            disabled={busy}
            onClick={async () => {
              if (!name.trim()) {
                toast("error", "Укажите название проекта");
                return;
              }
              setBusy(true);
              try {
                const gov = sources.find((s) => s.name === "government");
                const p = await createProject({
                  name: name.trim(),
                  source_ids: gov ? [gov.id] : sources.filter((s) => s.is_enabled).map((s) => s.id),
                });
                setName("");
                await reload();
                setSelectedId(p.id);
                toast("success", "Проект создан");
              } catch (e) {
                toast("error", e.message);
              } finally {
                setBusy(false);
              }
            }}
          >
            + Создать
          </button>
        </div>
        <input
          className="override-input"
          placeholder="Название проекта…"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ marginBottom: 8 }}
        />
        {projects.map((p) => (
          <button
            key={p.id}
            type="button"
            className={`source-row ${selectedId === p.id ? "active" : ""}`}
            onClick={() => setSelectedId(p.id)}
          >
            <div className="source-row-title">{p.name}</div>
            <div className="source-row-meta">
              {(p.catalog_links || []).length} каталог(ов)
            </div>
          </button>
        ))}
      </div>

      <div className="catalogs-main">
        {!selected ? (
          <p className="empty-hint">Создайте проект и привяжите каталоги поставщиков / гос. сметы</p>
        ) : (
          <>
            <h2 style={{ fontSize: 18, marginBottom: 8 }}>{selected.name}</h2>
            <p className="empty-hint" style={{ marginBottom: 14 }}>
              Отметьте каталоги для подбора в этом проекте. Снимите галочку, чтобы пропустить источник.
            </p>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Каталог</th>
                  <th>Тип</th>
                  <th>В подборе</th>
                </tr>
              </thead>
              <tbody>
                {sources.map((s, idx) => {
                  const linked = linkMap[s.id];
                  const included = linked ? linked.include_in_matching : false;
                  const checked = !!linked;
                  return (
                    <tr key={s.id}>
                      <td>{s.name}</td>
                      <td>{s.kind}</td>
                      <td>
                        <label className="chk-inline">
                          <input
                            type="checkbox"
                            checked={checked && included}
                            onChange={async (e) => {
                              const next = sources.map((src, i) => {
                                const prev = linkMap[src.id];
                                if (src.id === s.id) {
                                  return {
                                    source_id: src.id,
                                    include_in_matching: e.target.checked,
                                    sort_order: (prev?.sort_order ?? i * 10),
                                  };
                                }
                                if (!prev) return null;
                                return {
                                  source_id: src.id,
                                  include_in_matching: prev.include_in_matching,
                                  sort_order: prev.sort_order,
                                };
                              }).filter(Boolean);
                              // If enabling a previously unlinked source, add it
                              if (e.target.checked && !linkMap[s.id]) {
                                next.push({
                                  source_id: s.id,
                                  include_in_matching: true,
                                  sort_order: idx * 10,
                                });
                              }
                              // If unchecking, remove from links entirely (skip)
                              const cleaned = e.target.checked
                                ? next
                                : next.filter((l) => l.source_id !== s.id);
                              try {
                                const updated = await putProjectCatalogLinks(selected.id, cleaned);
                                setProjects((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
                              } catch (err) {
                                toast("error", err.message);
                              }
                            }}
                          />
                          Включить
                        </label>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            <div className="workflow-card" style={{ marginTop: 18, flexDirection: "column", alignItems: "flex-start" }}>
              <strong style={{ fontSize: 13, marginBottom: 8 }}>Позиции проекта (.xlsx)</strong>
              <div className="step-controls">
                <input type="file" accept=".xlsx" onChange={(e) => setItemsFile(e.target.files?.[0] || null)} />
                <button
                  className="btn btn-primary btn-sm"
                  disabled={!itemsFile || busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      const r = await uploadItems(itemsFile, selected.id);
                      toast("success", `Загружено ${r.rows_imported} позиций в проект`);
                      setItemsFile(null);
                    } catch (e) {
                      toast("error", e.message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Загрузить в проект
                </button>
              </div>
              <p className="empty-hint" style={{ marginTop: 8 }}>
                При запуске подбора с project_id используются только включённые каталоги проекта.
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
