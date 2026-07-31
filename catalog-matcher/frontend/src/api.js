const BASE = "/api";

async function handle(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return res;
}

export async function uploadCatalog(file, options = {}) {
  const {
    sourceName = "government",
    computeEmbeddings = false,
    importMode = "upsert",
    replaceExisting = false,
    versionLabel = null,
    deactivateMissing = false,
    kind = "government",
  } = options;
  const form = new FormData();
  form.append("file", file);
  const qs = new URLSearchParams({
    source_name: sourceName,
    import_mode: importMode,
    replace_existing: String(replaceExisting),
    deactivate_missing: String(deactivateMissing),
    kind,
  });
  if (computeEmbeddings) qs.set("compute_embeddings", "true");
  if (versionLabel) qs.set("version_label", versionLabel);
  const res = await fetch(`${BASE}/upload/catalog?${qs.toString()}`, {
    method: "POST",
    body: form,
  });
  await handle(res);
  return res.json();
}

export async function uploadItems(file, projectId = null) {
  const form = new FormData();
  form.append("file", file);
  const qs = new URLSearchParams();
  if (projectId != null) qs.set("project_id", String(projectId));
  const url = qs.toString() ? `${BASE}/upload/items?${qs}` : `${BASE}/upload/items`;
  const res = await fetch(url, { method: "POST", body: form });
  await handle(res);
  return res.json();
}

export async function fetchCatalogSources(includeArchived = false) {
  const qs = includeArchived ? "?include_archived=true" : "";
  const res = await fetch(`${BASE}/catalog-sources${qs}`);
  await handle(res);
  return res.json();
}

export async function createCatalogSource(payload) {
  const res = await fetch(`${BASE}/catalog-sources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await handle(res);
  return res.json();
}

export async function patchCatalogSource(sourceId, payload) {
  const res = await fetch(`${BASE}/catalog-sources/${sourceId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await handle(res);
  return res.json();
}

export async function fetchVersions(sourceId) {
  const res = await fetch(`${BASE}/catalog-sources/${sourceId}/versions`);
  await handle(res);
  return res.json();
}

export async function createVersion(sourceId, payload) {
  const res = await fetch(`${BASE}/catalog-sources/${sourceId}/versions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await handle(res);
  return res.json();
}

export async function setCurrentVersion(sourceId, versionId) {
  const res = await fetch(
    `${BASE}/catalog-sources/${sourceId}/versions/${versionId}/set-current`,
    { method: "POST" },
  );
  await handle(res);
  return res.json();
}

export async function fetchFields(sourceId) {
  const res = await fetch(`${BASE}/catalog-sources/${sourceId}/fields`);
  await handle(res);
  return res.json();
}

export async function createField(sourceId, payload) {
  const res = await fetch(`${BASE}/catalog-sources/${sourceId}/fields`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await handle(res);
  return res.json();
}

export async function patchField(sourceId, fieldId, payload) {
  const res = await fetch(`${BASE}/catalog-sources/${sourceId}/fields/${fieldId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await handle(res);
  return res.json();
}

export async function deleteField(sourceId, fieldId) {
  const res = await fetch(`${BASE}/catalog-sources/${sourceId}/fields/${fieldId}`, {
    method: "DELETE",
  });
  await handle(res);
  return res.json();
}

export async function reorderFields(sourceId, orderedKeys) {
  const res = await fetch(`${BASE}/catalog-sources/${sourceId}/fields/reorder`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ordered_keys: orderedKeys }),
  });
  await handle(res);
  return res.json();
}

export async function fetchImportMappings(sourceId) {
  const res = await fetch(`${BASE}/catalog-sources/${sourceId}/import-mappings`);
  await handle(res);
  return res.json();
}

export async function putImportMappings(sourceId, mappings) {
  const res = await fetch(`${BASE}/catalog-sources/${sourceId}/import-mappings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mappings }),
  });
  await handle(res);
  return res.json();
}

export async function previewImportHeaders(sourceId, file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(
    `${BASE}/catalog-sources/${sourceId}/import-mappings/preview-headers`,
    { method: "POST", body: form },
  );
  await handle(res);
  return res.json();
}

export async function fetchProducts(params = {}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v != null && v !== "") qs.set(k, String(v));
  });
  const res = await fetch(`${BASE}/catalog-products?${qs.toString()}`);
  await handle(res);
  return res.json();
}

export async function createProduct(sourceId, payload) {
  const res = await fetch(`${BASE}/catalog-sources/${sourceId}/products`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await handle(res);
  return res.json();
}

export async function patchProduct(productId, payload) {
  const res = await fetch(`${BASE}/catalog-products/${productId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await handle(res);
  return res.json();
}

export async function deactivateProduct(productId) {
  const res = await fetch(`${BASE}/catalog-products/${productId}`, { method: "DELETE" });
  await handle(res);
  return res.json();
}

export async function fetchCategories(sourceId) {
  const res = await fetch(`${BASE}/catalog/categories?source_id=${sourceId}`);
  await handle(res);
  return res.json();
}

export async function fetchProjects() {
  const res = await fetch(`${BASE}/projects`);
  await handle(res);
  return res.json();
}

export async function createProject(payload) {
  const res = await fetch(`${BASE}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await handle(res);
  return res.json();
}

export async function putProjectCatalogLinks(projectId, links) {
  const res = await fetch(`${BASE}/projects/${projectId}/catalog-links`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ links }),
  });
  await handle(res);
  return res.json();
}

export async function fetchMatchCapabilities() {
  const res = await fetch(`${BASE}/match/capabilities`);
  await handle(res);
  return res.json();
}

export async function fetchMatchStatus() {
  const res = await fetch(`${BASE}/match/status`);
  await handle(res);
  return res.json();
}

export async function cancelMatching() {
  const res = await fetch(`${BASE}/match/cancel`, { method: "POST" });
  await handle(res);
  return res.json();
}

export async function runMatching(options = {}) {
  const {
    sourceName = "government",
    sourceNames = null,
    sourceIds = null,
    projectId = null,
    matchingMode = "balanced",
    topKCandidates = null,
    topNResults = null,
    minSimilarityScore = null,
    useCodeMatching = null,
    useTfidf = null,
    useFuzzyText = null,
    useEmbeddings = null,
    embeddingModel = null,
    embedCatalogIfMissing = true,
    useCategoryFilter = null,
    inferCategoryIfMissing = null,
  } = options;

  const body = {
    source_name: sourceName,
    matching_mode: matchingMode,
    embed_catalog_if_missing: embedCatalogIfMissing,
  };
  if (sourceNames?.length) body.source_names = sourceNames;
  if (sourceIds?.length) body.source_ids = sourceIds;
  if (projectId != null) body.project_id = projectId;
  if (topKCandidates != null) body.top_k_candidates = topKCandidates;
  if (topNResults != null) body.top_n_results = topNResults;
  if (minSimilarityScore != null) body.min_similarity_score = minSimilarityScore;
  if (useCodeMatching != null) body.use_code_matching = useCodeMatching;
  if (useTfidf != null) body.use_tfidf = useTfidf;
  if (useFuzzyText != null) body.use_fuzzy_text = useFuzzyText;
  if (useEmbeddings != null) body.use_embeddings = useEmbeddings;
  if (embeddingModel != null) body.embedding_model = embeddingModel;
  if (useCategoryFilter != null) body.use_category_filter = useCategoryFilter;
  if (inferCategoryIfMissing != null) body.infer_category_if_missing = inferCategoryIfMissing;

  const res = await fetch(`${BASE}/match/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await handle(res);
  return res.json();
}

export async function fetchItems(projectId = null) {
  const qs = projectId != null ? `?project_id=${projectId}` : "";
  const res = await fetch(`${BASE}/items${qs}`);
  await handle(res);
  return res.json();
}

export async function selectMatch(itemId, catalogProductId) {
  const res = await fetch(`${BASE}/match/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId, catalog_product_id: catalogProductId }),
  });
  await handle(res);
  return res.json();
}

export async function searchCatalogProducts(q, sourceName = "government", sourceIds = null) {
  const qs = new URLSearchParams({ q, source_name: sourceName });
  if (sourceIds?.length) qs.set("source_ids", sourceIds.join(","));
  const res = await fetch(`${BASE}/catalog-products/search?${qs.toString()}`);
  await handle(res);
  return res.json();
}

function downloadBlobResponse(res, fallbackFilename) {
  return res.blob().then((blob) => {
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : fallbackFilename;

    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  });
}

export async function exportResults(minConfidence = null) {
  const qs = minConfidence != null ? `?min_confidence=${minConfidence}` : "";
  const res = await fetch(`${BASE}/export${qs}`, { method: "POST" });
  await handle(res);
  await downloadBlobResponse(res, "Export.xlsx");
}

export async function exportResultsBatched(minConfidence = 0.8, batchSize = 100) {
  const params = new URLSearchParams({
    min_confidence: minConfidence,
    batch_size: batchSize,
  });
  const res = await fetch(`${BASE}/export/batches?${params.toString()}`, { method: "POST" });
  await handle(res);
  await downloadBlobResponse(res, "Best_Matches_batches.zip");
}
