"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CatalogMergeResult,
  MasterProductCreatePayload,
  MasterProductRow,
  MasterProductUpdatePayload,
  createCatalogProduct,
  deleteCatalogProduct,
  listCatalogProducts,
  restoreCatalogProduct,
  updateCatalogFromFile,
  updateCatalogProduct,
} from "@/lib/api";

const PAGE_SIZE = 50;

type EditDraft = MasterProductUpdatePayload & { product_name?: string | null };

function money(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("ru-RU");
}

export default function CatalogTab() {
  const [items, setItems] = useState<MasterProductRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [q, setQ] = useState("");
  const [qDraft, setQDraft] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [catalogVersionId, setCatalogVersionId] = useState<string | null>(null);
  const [catalogVersionName, setCatalogVersionName] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<EditDraft>({});
  const [saving, setSaving] = useState(false);

  const [undoRow, setUndoRow] = useState<MasterProductRow | null>(null);
  const [mergeResult, setMergeResult] = useState<CatalogMergeResult | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [addDraft, setAddDraft] = useState<MasterProductCreatePayload>({ product_name: "" });

  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listCatalogProducts({
        q: q || undefined,
        limit: PAGE_SIZE,
        offset,
        includeInactive,
      });
      setItems(res.items);
      setTotal(res.total);
      setCatalogVersionId(res.catalog_version_id);
      setCatalogVersionName(res.catalog_version_name);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load catalog");
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [q, offset, includeInactive]);

  useEffect(() => {
    load();
  }, [load]);

  const startEdit = (row: MasterProductRow) => {
    setEditingId(row.id);
    setDraft({
      product_name: row.product_name,
      external_id: row.external_id,
      description: row.description,
      unit: row.unit,
      price: row.price,
      material: row.material,
      dim_w_mm: row.dim_w_mm,
      dim_h_mm: row.dim_h_mm,
      dim_d_mm: row.dim_d_mm,
    });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setDraft({});
  };

  const saveEdit = async () => {
    if (!editingId) return;
    setSaving(true);
    setError(null);
    try {
      const payload: MasterProductUpdatePayload = { ...draft };
      if (typeof payload.price === "number" && Number.isNaN(payload.price)) payload.price = null;
      await updateCatalogProduct(editingId, payload);
      cancelEdit();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async (row: MasterProductRow) => {
    if (!window.confirm(`Удалить «${row.product_name || row.id}»?`)) return;
    setError(null);
    try {
      const deleted = await deleteCatalogProduct(row.id);
      setUndoRow(deleted);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  const onRestore = async (id: string) => {
    setError(null);
    try {
      await restoreCatalogProduct(id);
      setUndoRow(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Restore failed");
    }
  };

  const onRefreshFile = async (file: File | undefined) => {
    if (!file || !catalogVersionId) return;
    setError(null);
    setMergeResult(null);
    try {
      const result = await updateCatalogFromFile(catalogVersionId, file);
      setMergeResult(result);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refresh failed");
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const onAdd = async () => {
    if (!addDraft.product_name.trim()) {
      setError("Укажите наименование");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await createCatalogProduct({
        ...addDraft,
        product_name: addDraft.product_name.trim(),
      });
      setShowAdd(false);
      setAddDraft({ product_name: "" });
      setOffset(0);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Create failed");
    } finally {
      setSaving(false);
    }
  };

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Catalog</h2>
          <p className="text-sm text-gray-500">
            {catalogVersionName
              ? `Активная версия: ${catalogVersionName}`
              : "Нет активной CatalogVersion — показаны все master-строки"}
            {total > 0 ? ` · ${total.toLocaleString("ru-RU")} поз.` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-sm text-gray-600">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => {
                setIncludeInactive(e.target.checked);
                setOffset(0);
              }}
            />
            Show deleted
          </label>
          <button
            type="button"
            className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            onClick={() => setShowAdd((v) => !v)}
          >
            + Add product
          </button>
          <button
            type="button"
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!catalogVersionId}
            title={
              catalogVersionId
                ? "Merge a newer catalog Excel into the active version"
                : "Нет активной версии каталога для merge"
            }
            onClick={() => fileInputRef.current?.click()}
          >
            Refresh from file…
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xls,.csv"
            className="hidden"
            onChange={(e) => onRefreshFile(e.target.files?.[0])}
          />
        </div>
      </div>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setOffset(0);
          setQ(qDraft.trim());
        }}
      >
        <input
          value={qDraft}
          onChange={(e) => setQDraft(e.target.value)}
          placeholder="Поиск по названию или коду…"
          className="min-w-0 flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm"
        />
        <button
          type="submit"
          className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium hover:bg-gray-50"
        >
          Найти
        </button>
      </form>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</div>
      )}
      {undoRow && (
        <div className="flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <span>Удалено: {undoRow.product_name || undoRow.id}</span>
          <button type="button" className="font-semibold underline" onClick={() => onRestore(undoRow.id)}>
            Undo
          </button>
        </div>
      )}
      {mergeResult && (
        <div className="flex items-start justify-between gap-3 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
          <span>
            Refresh: {mergeResult.updated} updated
            {mergeResult.reactivated ? ` (${mergeResult.reactivated} reactivated)` : ""},{" "}
            {mergeResult.inserted} inserted, {mergeResult.unmatched_existing} left untouched
            {mergeResult.errors?.length ? `, ${mergeResult.errors.length} row errors` : ""}. Active:{" "}
            {mergeResult.total_active_products.toLocaleString("ru-RU")}
          </span>
          <button type="button" className="font-semibold underline" onClick={() => setMergeResult(null)}>
            Dismiss
          </button>
        </div>
      )}

      {showAdd && (
        <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div className="mb-3 text-sm font-semibold text-gray-800">Новая позиция</div>
          <div className="grid gap-2 sm:grid-cols-2">
            <input
              className="rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="Наименование *"
              value={addDraft.product_name}
              onChange={(e) => setAddDraft({ ...addDraft, product_name: e.target.value })}
            />
            <input
              className="rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="Код"
              value={addDraft.external_id ?? ""}
              onChange={(e) => setAddDraft({ ...addDraft, external_id: e.target.value || null })}
            />
            <input
              className="rounded-md border border-gray-300 px-3 py-2 text-sm sm:col-span-2"
              placeholder="Описание"
              value={addDraft.description ?? ""}
              onChange={(e) => setAddDraft({ ...addDraft, description: e.target.value || null })}
            />
            <input
              className="rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="Ед."
              value={addDraft.unit ?? ""}
              onChange={(e) => setAddDraft({ ...addDraft, unit: e.target.value || null })}
            />
            <input
              className="rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="Цена"
              type="number"
              value={addDraft.price ?? ""}
              onChange={(e) =>
                setAddDraft({
                  ...addDraft,
                  price: e.target.value === "" ? null : Number(e.target.value),
                })
              }
            />
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              disabled={saving}
              className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              onClick={onAdd}
            >
              Сохранить
            </button>
            <button
              type="button"
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
              onClick={() => setShowAdd(false)}
            >
              Отмена
            </button>
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full table-fixed divide-y divide-gray-200 text-sm">
            <colgroup>
              <col className="w-[14%]" />
              <col className="w-[46%]" />
              <col className="w-[8%]" />
              <col className="w-[10%]" />
              <col className="w-[10%]" />
              <col className="w-[12%]" />
            </colgroup>
            <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-3 py-2">Код</th>
                <th className="px-3 py-2">Наименование</th>
                <th className="px-3 py-2">Ед.</th>
                <th className="px-3 py-2">Цена</th>
                <th className="px-3 py-2">Статус</th>
                <th className="px-3 py-2 text-right">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading && (
                <tr>
                  <td colSpan={6} className="px-3 py-8 text-center text-gray-500">
                    Загрузка…
                  </td>
                </tr>
              )}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-8 text-center text-gray-500">
                    Нет строк. Загрузите каталог через Batch match или добавьте вручную.
                  </td>
                </tr>
              )}
              {!loading &&
                items.map((row) => {
                  const editing = editingId === row.id;
                  return (
                    <tr key={row.id} className={!row.is_active ? "bg-gray-50 text-gray-400" : undefined}>
                      <td className="px-3 py-2 align-top">
                        {editing ? (
                          <input
                            className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
                            value={draft.external_id ?? ""}
                            onChange={(e) => setDraft({ ...draft, external_id: e.target.value || null })}
                          />
                        ) : (
                          <div className="break-words whitespace-normal text-xs leading-snug text-gray-700">
                            {row.external_id || "—"}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2 align-top">
                        {editing ? (
                          <div className="space-y-1">
                            <textarea
                              className="w-full resize-y rounded border border-gray-300 px-2 py-1 text-sm leading-snug"
                              rows={3}
                              value={draft.product_name ?? ""}
                              onChange={(e) => setDraft({ ...draft, product_name: e.target.value || null })}
                            />
                            <textarea
                              className="w-full resize-y rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 leading-snug"
                              rows={2}
                              placeholder="Описание"
                              value={draft.description ?? ""}
                              onChange={(e) => setDraft({ ...draft, description: e.target.value || null })}
                            />
                          </div>
                        ) : (
                          <div className="min-w-0">
                            <div className="break-words whitespace-normal font-medium leading-snug text-gray-900">
                              {row.product_name || "—"}
                            </div>
                            {row.description && (
                              <div className="mt-1 break-words whitespace-normal text-xs leading-snug text-gray-500">
                                {row.description}
                              </div>
                            )}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2 align-top">
                        {editing ? (
                          <input
                            className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
                            value={draft.unit ?? ""}
                            onChange={(e) => setDraft({ ...draft, unit: e.target.value || null })}
                          />
                        ) : (
                          <span className="break-words whitespace-normal">{row.unit || "—"}</span>
                        )}
                      </td>
                      <td className="px-3 py-2 align-top tabular-nums">
                        {editing ? (
                          <input
                            type="number"
                            className="w-24 rounded border border-gray-300 px-2 py-1 text-sm"
                            value={draft.price ?? ""}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                price: e.target.value === "" ? null : Number(e.target.value),
                              })
                            }
                          />
                        ) : (
                          money(row.price)
                        )}
                      </td>
                      <td className="px-3 py-2 align-top">
                        <span
                          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${
                            row.is_active ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"
                          }`}
                        >
                          {row.is_active ? "active" : "deleted"}
                        </span>
                      </td>
                      <td className="px-3 py-2 align-top text-right whitespace-nowrap">
                        {editing ? (
                          <div className="flex justify-end gap-2">
                            <button
                              type="button"
                              disabled={saving}
                              className="text-sm font-semibold text-blue-700 hover:underline disabled:opacity-50"
                              onClick={saveEdit}
                            >
                              Save
                            </button>
                            <button type="button" className="text-sm text-gray-500 hover:underline" onClick={cancelEdit}>
                              Cancel
                            </button>
                          </div>
                        ) : row.is_active ? (
                          <div className="flex justify-end gap-2">
                            <button
                              type="button"
                              className="text-sm font-medium text-blue-700 hover:underline"
                              onClick={() => startEdit(row)}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="text-sm font-medium text-red-600 hover:underline"
                              onClick={() => onDelete(row)}
                            >
                              Delete
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            className="text-sm font-medium text-blue-700 hover:underline"
                            onClick={() => onRestore(row.id)}
                          >
                            Restore
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-gray-100 px-3 py-2 text-sm text-gray-600">
          <span>
            Стр. {page} / {pages}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              className="rounded border border-gray-300 px-2 py-1 disabled:opacity-40"
              disabled={offset <= 0 || loading}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              ← Prev
            </button>
            <button
              type="button"
              className="rounded border border-gray-300 px-2 py-1 disabled:opacity-40"
              disabled={offset + PAGE_SIZE >= total || loading}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next →
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
