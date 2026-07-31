import { useCallback, useEffect, useState } from "react";
import {
  createCatalogSource,
  createField,
  createProduct,
  createVersion,
  deactivateProduct,
  deleteField,
  fetchCategories,
  fetchCatalogSources,
  fetchFields,
  fetchImportMappings,
  fetchProducts,
  fetchVersions,
  patchCatalogSource,
  patchField,
  patchProduct,
  previewImportHeaders,
  putImportMappings,
  reorderFields,
  setCurrentVersion,
  uploadCatalog,
} from "./api.js";

function fmtPrice(v) {
  if (v == null) return "—";
  return "₸ " + Number(v).toLocaleString("ru-RU");
}

export default function CatalogsPanel({ onToast }) {
  const toast = onToast || (() => {});
  const [sources, setSources] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [fields, setFields] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [products, setProducts] = useState([]);
  const [versions, setVersions] = useState([]);
  const [categories, setCategories] = useState([]);
  const [collapsedCats, setCollapsedCats] = useState({});
  const [tab, setTab] = useState("products"); // products | fields | mapping | upload
  const [q, setQ] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [editProduct, setEditProduct] = useState(null);
  const [newField, setNewField] = useState({ key: "", label: "", field_type: "string" });
  const [uploadFile, setUploadFile] = useState(null);
  const [versionLabel, setVersionLabel] = useState("");
  const [importMode, setImportMode] = useState("upsert");
  const [newSourceName, setNewSourceName] = useState("");
  const [previewHeaders, setPreviewHeaders] = useState([]);
  const [busy, setBusy] = useState(false);

  const selected = sources.find((s) => s.id === selectedId) || null;

  const reloadSources = useCallback(async () => {
    const list = await fetchCatalogSources(true);
    setSources(list);
    if (!selectedId && list.length) setSelectedId(list[0].id);
    if (selectedId && !list.find((s) => s.id === selectedId) && list.length) {
      setSelectedId(list[0].id);
    }
  }, [selectedId]);

  const reloadSourceDetail = useCallback(async () => {
    if (!selectedId) return;
    const [f, m, p, v, c] = await Promise.all([
      fetchFields(selectedId),
      fetchImportMappings(selectedId),
      fetchProducts({ source_id: selectedId, q: q || undefined, category_code: categoryFilter || undefined, limit: 200 }),
      fetchVersions(selectedId),
      fetchCategories(selectedId).catch(() => []),
    ]);
    setFields(f);
    setMappings(m);
    setProducts(p);
    setVersions(v);
    setCategories(c);
  }, [selectedId, q, categoryFilter]);

  useEffect(() => {
    reloadSources().catch((e) => toast("error", e.message));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    reloadSourceDetail().catch((e) => toast("error", e.message));
  }, [reloadSourceDetail]);

  const visibleFields = fields.filter((f) => f.show_in_table).sort((a, b) => a.sort_order - b.sort_order);

  const productsByCategory = () => {
    const groups = {};
    for (const p of products) {
      const key = p.category_code || "_none";
      if (!groups[key]) {
        groups[key] = {
          code: p.category_code,
          name: p.category_name || (p.category_code ? p.category_code : "Без категории"),
          items: [],
        };
      }
      groups[key].items.push(p);
    }
    return Object.values(groups);
  };

  const toggleCat = (code) => {
    const key = code || "_none";
    setCollapsedCats((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const moveField = async (index, dir) => {
    const ordered = [...fields].sort((a, b) => a.sort_order - b.sort_order);
    const j = index + dir;
    if (j < 0 || j >= ordered.length) return;
    const tmp = ordered[index];
    ordered[index] = ordered[j];
    ordered[j] = tmp;
    await reorderFields(selectedId, ordered.map((f) => f.key));
    await reloadSourceDetail();
  };

  return (
    <div className="catalogs-panel">
      <div className="catalogs-sidebar">
        <div className="catalogs-sidebar-head">
          <strong>Каталоги</strong>
          <button
            className="btn btn-ghost btn-sm"
            disabled={busy}
            onClick={async () => {
              const name = newSourceName.trim() || prompt("Имя каталога (например supplier_acme)");
              if (!name) return;
              setBusy(true);
              try {
                const s = await createCatalogSource({
                  name,
                  kind: name === "government" ? "government" : "supplier",
                  description: name,
                });
                setNewSourceName("");
                await reloadSources();
                setSelectedId(s.id);
                toast("success", `Каталог «${s.name}» создан`);
              } catch (e) {
                toast("error", e.message);
              } finally {
                setBusy(false);
              }
            }}
          >
            + Новый
          </button>
        </div>
        <input
          className="override-input"
          placeholder="имя нового каталога…"
          value={newSourceName}
          onChange={(e) => setNewSourceName(e.target.value)}
          style={{ marginBottom: 8 }}
        />
        {sources.map((s) => (
          <button
            key={s.id}
            type="button"
            className={`source-row ${selectedId === s.id ? "active" : ""} ${!s.is_enabled ? "disabled-source" : ""}`}
            onClick={() => setSelectedId(s.id)}
          >
            <div className="source-row-title">
              <span className={`source-kind ${s.kind}`}>{s.kind === "government" ? "Гос" : "Пост."}</span>
              {s.name}
            </div>
            <div className="source-row-meta">
              {s.product_count} поз. · {s.current_version_label || "—"}
              {!s.is_enabled && " · пропуск"}
            </div>
          </button>
        ))}
      </div>

      <div className="catalogs-main">
        {!selected ? (
          <p className="empty-hint">Выберите или создайте каталог</p>
        ) : (
          <>
            <div className="catalogs-toolbar">
              <div>
                <h2 style={{ fontSize: 18, marginBottom: 4 }}>{selected.name}</h2>
                <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
                  {selected.description || "—"} · версия {selected.current_version_label || "—"}
                </div>
              </div>
              <div className="step-controls">
                <label className="chk-inline">
                  <input
                    type="checkbox"
                    checked={selected.is_enabled}
                    onChange={async (e) => {
                      try {
                        await patchCatalogSource(selected.id, { is_enabled: e.target.checked });
                        await reloadSources();
                        toast("success", e.target.checked ? "Каталог включён" : "Каталог пропускается при подборе");
                      } catch (err) {
                        toast("error", err.message);
                      }
                    }}
                  />
                  Участвует в подборе
                </label>
              </div>
            </div>

            <div className="filter-tabs" style={{ marginBottom: 14 }}>
              {[
                ["products", "Позиции"],
                ["fields", "Поля / дизайнер"],
                ["mapping", "Маппинг Excel"],
                ["upload", "Загрузка / версии"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  className={`filter-tab ${tab === id ? "active" : ""}`}
                  onClick={() => setTab(id)}
                >
                  {label}
                </button>
              ))}
            </div>

            {tab === "products" && (
              <div>
                <div className="step-controls" style={{ marginBottom: 12 }}>
                  <input
                    className="override-input"
                    placeholder="Поиск…"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    style={{ maxWidth: 240 }}
                  />
                  <select
                    className="mode-select"
                    value={categoryFilter}
                    onChange={(e) => setCategoryFilter(e.target.value)}
                  >
                    <option value="">Все категории</option>
                    {categories.map((c) => (
                      <option key={c.category_code} value={c.category_code}>
                        {c.category_name || c.category_code} ({c.product_count})
                      </option>
                    ))}
                  </select>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={async () => {
                      setEditProduct({
                        name: "",
                        code: "",
                        brand: "",
                        model: "",
                        price: "",
                        description: "",
                        technical_specs: "",
                        custom_fields: {},
                        _new: true,
                      });
                    }}
                  >
                    + Позиция
                  </button>
                </div>

                {productsByCategory().map((group) => {
                  const key = group.code || "_none";
                  const collapsed = !!collapsedCats[key];
                  return (
                    <div key={key} className="cat-group">
                      <button type="button" className="cat-group-head" onClick={() => toggleCat(group.code)}>
                        <span>{collapsed ? "▸" : "▾"}</span>
                        <strong>{group.name}</strong>
                        <span className="source-row-meta">{group.items.length}</span>
                      </button>
                      {!collapsed && (
                        <table className="data-table">
                          <thead>
                            <tr>
                              {visibleFields.slice(0, 6).map((f) => (
                                <th key={f.key}>{f.label}</th>
                              ))}
                              <th />
                            </tr>
                          </thead>
                          <tbody>
                            {group.items.map((p) => (
                              <tr key={p.id}>
                                {visibleFields.slice(0, 6).map((f) => {
                                  let val = p[f.key];
                                  if (f.key === "category") val = p.category_name;
                                  if (val == null && p.custom_fields) val = p.custom_fields[f.key];
                                  if (f.key === "price") val = fmtPrice(p.price);
                                  return <td key={f.key}>{val ?? "—"}</td>;
                                })}
                                <td>
                                  <button className="btn btn-ghost btn-sm" onClick={() => setEditProduct({ ...p })}>
                                    Изм.
                                  </button>
                                  <button
                                    className="btn btn-ghost btn-sm"
                                    onClick={async () => {
                                      if (!confirm("Деактивировать позицию?")) return;
                                      await deactivateProduct(p.id);
                                      await reloadSourceDetail();
                                      await reloadSources();
                                    }}
                                  >
                                    Скрыть
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {tab === "fields" && (
              <div>
                <p className="empty-hint" style={{ marginBottom: 12 }}>
                  Ядро полей нельзя удалить. Добавляйте свои поля и меняйте порядок / видимость.
                </p>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Порядок</th>
                      <th>Ключ</th>
                      <th>Подпись</th>
                      <th>Тип</th>
                      <th>В таблице</th>
                      <th>В подборе</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {[...fields].sort((a, b) => a.sort_order - b.sort_order).map((f, idx) => (
                      <tr key={f.id}>
                        <td>
                          <button className="btn btn-ghost btn-sm" onClick={() => moveField(idx, -1)}>↑</button>
                          <button className="btn btn-ghost btn-sm" onClick={() => moveField(idx, 1)}>↓</button>
                        </td>
                        <td><code>{f.key}</code>{f.is_core ? " · core" : ""}</td>
                        <td>
                          <input
                            className="override-input"
                            value={f.label}
                            onChange={(e) => {
                              const label = e.target.value;
                              setFields((prev) => prev.map((x) => (x.id === f.id ? { ...x, label } : x)));
                            }}
                            onBlur={async (e) => {
                              await patchField(selectedId, f.id, { label: e.target.value });
                            }}
                          />
                        </td>
                        <td>{f.field_type}</td>
                        <td>
                          <input
                            type="checkbox"
                            checked={f.show_in_table}
                            onChange={async (e) => {
                              await patchField(selectedId, f.id, { show_in_table: e.target.checked });
                              await reloadSourceDetail();
                            }}
                          />
                        </td>
                        <td>
                          <input
                            type="checkbox"
                            checked={f.use_in_matching}
                            onChange={async (e) => {
                              await patchField(selectedId, f.id, { use_in_matching: e.target.checked });
                              await reloadSourceDetail();
                            }}
                          />
                        </td>
                        <td>
                          {!f.is_core && (
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={async () => {
                                await deleteField(selectedId, f.id);
                                await reloadSourceDetail();
                              }}
                            >
                              Удалить
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="step-controls" style={{ marginTop: 12 }}>
                  <input className="override-input" placeholder="key" value={newField.key}
                    onChange={(e) => setNewField({ ...newField, key: e.target.value })} />
                  <input className="override-input" placeholder="Подпись" value={newField.label}
                    onChange={(e) => setNewField({ ...newField, label: e.target.value })} />
                  <select className="mode-select" value={newField.field_type}
                    onChange={(e) => setNewField({ ...newField, field_type: e.target.value })}>
                    <option value="string">string</option>
                    <option value="number">number</option>
                    <option value="text">text</option>
                  </select>
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={async () => {
                      if (!newField.key || !newField.label) return;
                      await createField(selectedId, newField);
                      setNewField({ key: "", label: "", field_type: "string" });
                      await reloadSourceDetail();
                      toast("success", "Поле добавлено");
                    }}
                  >
                    Добавить поле
                  </button>
                </div>
              </div>
            )}

            {tab === "mapping" && (
              <div>
                <p className="empty-hint" style={{ marginBottom: 12 }}>
                  Сопоставьте заголовки Excel с полями каталога. Можно подтянуть заголовки из файла.
                </p>
                <div className="step-controls" style={{ marginBottom: 12 }}>
                  <input
                    type="file"
                    accept=".xlsx"
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      try {
                        const r = await previewImportHeaders(selectedId, file);
                        setPreviewHeaders(r.headers || []);
                      } catch (err) {
                        toast("error", err.message);
                      }
                    }}
                  />
                </div>
                {previewHeaders.length > 0 && (
                  <div style={{ marginBottom: 12, fontSize: 12 }}>
                    Заголовки файла: {previewHeaders.join(" · ")}
                  </div>
                )}
                <table className="data-table">
                  <thead>
                    <tr><th>Заголовок Excel</th><th>Поле</th><th /></tr>
                  </thead>
                  <tbody>
                    {mappings.map((m, idx) => (
                      <tr key={`${m.excel_header}-${idx}`}>
                        <td>
                          <input
                            className="override-input"
                            value={m.excel_header}
                            onChange={(e) => {
                              const excel_header = e.target.value;
                              setMappings((prev) => prev.map((x, i) => (i === idx ? { ...x, excel_header } : x)));
                            }}
                          />
                        </td>
                        <td>
                          <select
                            className="mode-select"
                            value={m.field_key}
                            onChange={(e) => {
                              const field_key = e.target.value;
                              setMappings((prev) => prev.map((x, i) => (i === idx ? { ...x, field_key } : x)));
                            }}
                          >
                            {fields.map((f) => (
                              <option key={f.key} value={f.key}>{f.label} ({f.key})</option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => setMappings((prev) => prev.filter((_, i) => i !== idx))}
                          >
                            ×
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="step-controls" style={{ marginTop: 12 }}>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => setMappings((prev) => [...prev, { excel_header: "", field_key: "name" }])}
                  >
                    + Строка
                  </button>
                  {previewHeaders.map((h) => (
                    <button
                      key={h}
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setMappings((prev) => [...prev, { excel_header: h.toLowerCase(), field_key: "name" }])}
                    >
                      + {h}
                    </button>
                  ))}
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={async () => {
                      await putImportMappings(
                        selectedId,
                        mappings.map((m) => ({ excel_header: m.excel_header, field_key: m.field_key })),
                      );
                      toast("success", "Маппинг сохранён");
                      await reloadSourceDetail();
                    }}
                  >
                    Сохранить маппинг
                  </button>
                </div>
              </div>
            )}

            {tab === "upload" && (
              <div>
                <div className="workflow-card" style={{ flexDirection: "column", alignItems: "stretch", gap: 12 }}>
                  <div className="step-controls">
                    <input type="file" accept=".xlsx" onChange={(e) => setUploadFile(e.target.files?.[0] || null)} />
                    <input
                      className="override-input"
                      placeholder="Метка версии (напр. 2026-Q2)"
                      value={versionLabel}
                      onChange={(e) => setVersionLabel(e.target.value)}
                      style={{ maxWidth: 200 }}
                    />
                    <select className="mode-select" value={importMode} onChange={(e) => setImportMode(e.target.value)}>
                      <option value="upsert">Upsert (обновить / добавить)</option>
                      <option value="replace">Replace (заменить версию)</option>
                    </select>
                    <button
                      className="btn btn-primary"
                      disabled={!uploadFile || busy}
                      onClick={async () => {
                        setBusy(true);
                        try {
                          const r = await uploadCatalog(uploadFile, {
                            sourceName: selected.name,
                            importMode,
                            replaceExisting: importMode === "replace",
                            versionLabel: versionLabel || null,
                            kind: selected.kind,
                          });
                          toast("success", r.message);
                          setUploadFile(null);
                          await reloadSources();
                          await reloadSourceDetail();
                        } catch (e) {
                          toast("error", e.message);
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      Загрузить
                    </button>
                  </div>
                  <div>
                    <strong style={{ fontSize: 13 }}>Версии</strong>
                    <ul style={{ marginTop: 8, fontSize: 13, lineHeight: 1.7 }}>
                      {versions.map((v) => (
                        <li key={v.id}>
                          {v.label} — {v.product_count} поз.
                          {v.is_current ? " · текущая" : (
                            <button
                              className="btn btn-ghost btn-sm"
                              style={{ marginLeft: 8 }}
                              onClick={async () => {
                                await setCurrentVersion(selectedId, v.id);
                                await reloadSources();
                                await reloadSourceDetail();
                              }}
                            >
                              Сделать текущей
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                    <div className="step-controls" style={{ marginTop: 8 }}>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={async () => {
                          const label = prompt("Метка новой версии");
                          if (!label) return;
                          await createVersion(selectedId, { label, set_current: true });
                          await reloadSources();
                          await reloadSourceDetail();
                        }}
                      >
                        + Версия
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {editProduct && (
        <div className="drawer-backdrop" onClick={() => setEditProduct(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginBottom: 12 }}>{editProduct._new ? "Новая позиция" : "Редактирование"}</h3>
            {["code", "name", "brand", "model", "price", "description", "technical_specs"].map((key) => (
              <label key={key} className="drawer-field">
                {key}
                <input
                  className="override-input"
                  value={editProduct[key] ?? ""}
                  onChange={(e) => setEditProduct({ ...editProduct, [key]: e.target.value })}
                />
              </label>
            ))}
            <div className="step-controls" style={{ marginTop: 16 }}>
              <button className="btn btn-ghost" onClick={() => setEditProduct(null)}>Отмена</button>
              <button
                className="btn btn-primary"
                onClick={async () => {
                  const payload = {
                    code: editProduct.code || null,
                    name: editProduct.name,
                    brand: editProduct.brand || null,
                    model: editProduct.model || null,
                    description: editProduct.description || null,
                    technical_specs: editProduct.technical_specs || null,
                    price: editProduct.price === "" || editProduct.price == null
                      ? null
                      : Number(editProduct.price),
                    custom_fields: editProduct.custom_fields || {},
                  };
                  if (!payload.name) {
                    toast("error", "Нужно наименование");
                    return;
                  }
                  if (editProduct._new) {
                    await createProduct(selectedId, payload);
                  } else {
                    await patchProduct(editProduct.id, payload);
                  }
                  setEditProduct(null);
                  await reloadSourceDetail();
                  await reloadSources();
                  toast("success", "Сохранено");
                }}
              >
                Сохранить
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
