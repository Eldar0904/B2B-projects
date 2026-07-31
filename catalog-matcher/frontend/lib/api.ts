const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface Candidate {
  master_product_id: string;
  external_id: string | null;
  product_name: string | null;
  /** Catalog "Описание" - the specs a reviewer needs to judge the match. */
  description: string | null;
  unit: string | null;
  price: number | null;
  final_score: number;
  embedding_score: number;
  keyword_score: number;
  fuzzy_name_score: number;
  /** Share of the request's key terms this candidate actually covers. */
  lexical_overlap_score: number;
  matched_by: string[];
  explanation: string[];
}

export interface NextProduct {
  destination_product_id: string;
  product_name: string | null;
  /** The requested item's own description, to compare against candidates. */
  description: string | null;
  quantity: number | null;
  price: number | null;
  candidates: Candidate[];
  confidence_level: "high" | "medium" | "low";
  no_reliable_match: boolean;
}

export interface Progress {
  total: number;
  pending: number;
  matched: number;
  no_match: number;
}

export interface AutoMatchResult {
  checked: number;
  auto_matched: number;
  exact_matches: number;
  threshold_matches: number;
  auto_rejected: number;
  still_pending: number;
}

export interface PrioritizeResult {
  checked: number;
  computed: number;
  insufficient_candidates: number;
}

export interface UploadInfo {
  id: string;
  filename: string;
  upload_type: string;
  sheet_name: string | null;
  status: string;
  total_rows: number;
  processed_rows: number;
  skipped_rows: number;
  error_report: Array<Record<string, unknown>> | null;
}

export interface SheetInfo {
  name: string;
  row_count: number;
  detected_header_row: number;
  columns: string[];
}

export interface UploadResult {
  upload: UploadInfo;
  sheets: SheetInfo[] | null;
}

export interface ReindexResult {
  total_records: number;
  indexed_records: number;
  group_headers_excluded: number;
  embedding_dim: number;
}

export type ReviewStrategy = "sequential" | "uncertainty";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Request failed: ${res.status}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

async function uploadFile<T>(path: string, file: File): Promise<T> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE_URL}${path}`, { method: "POST", body: formData });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function uploadMasterFile(file: File): Promise<UploadResult> {
  return uploadFile<UploadResult>("/api/uploads/master", file);
}

export function uploadDestinationFile(file: File): Promise<UploadResult> {
  return uploadFile<UploadResult>("/api/uploads/destination", file);
}

export function reindexSearch(): Promise<ReindexResult> {
  return request<ReindexResult>("/api/search/reindex", { method: "POST" });
}

export interface FeedbackStats {
  total: number;
  user_selected: number;
  manual_search_selected: number;
  no_match: number;
  auto_accepted: number;
  user_rejected: number;
  auto_rejected: number;
}

export interface TrainingReadiness {
  total_examples: number;
  positive_examples: number;
  negative_examples: number;
  min_required: number;
  ready: boolean;
  examples_needed: number;
}

export interface TrainResult {
  n_total: number;
  n_train: number;
  n_test: number;
  n_positive: number;
  n_negative: number;
  baseline_auc: number | null;
  model_auc: number | null;
  improvement: number | null;
  should_deploy: boolean;
  reason: string;
}

export function getFeedbackStats(uploadId: string): Promise<FeedbackStats> {
  return request<FeedbackStats>(`/api/matching/${uploadId}/feedback-stats`);
}

export function getTrainingReadiness(uploadId?: string): Promise<TrainingReadiness> {
  const query = uploadId ? `?upload_id=${uploadId}` : "";
  return request<TrainingReadiness>(`/api/ml/training-readiness${query}`);
}

export function trainModel(uploadId?: string): Promise<TrainResult> {
  const query = uploadId ? `?upload_id=${uploadId}` : "";
  return request<TrainResult>(`/api/ml/train${query}`, { method: "POST" });
}

export function listUploads(uploadType?: "master" | "destination"): Promise<UploadInfo[]> {
  const query = uploadType ? `?upload_type=${uploadType}` : "";
  return request<UploadInfo[]>(`/api/uploads${query}`);
}

export function startMatching(uploadId: string): Promise<Progress> {
  return request<Progress>(`/api/matching/${uploadId}/start`, { method: "POST" });
}

export function getProgress(uploadId: string): Promise<Progress> {
  return request<Progress>(`/api/matching/${uploadId}/progress`);
}

export function getNextProduct(
  uploadId: string,
  strategy: ReviewStrategy = "sequential"
): Promise<NextProduct | null> {
  return request<NextProduct | null>(`/api/matching/${uploadId}/next?strategy=${strategy}`);
}

export function confirmMatch(
  uploadId: string,
  destinationProductId: string,
  masterProductId: string,
  rank: number
): Promise<{ match_id: string; status: string }> {
  return request(`/api/matching/${uploadId}/confirm`, {
    method: "POST",
    body: JSON.stringify({
      destination_product_id: destinationProductId,
      master_product_id: masterProductId,
      rank,
    }),
  });
}

export function rejectMatch(
  uploadId: string,
  destinationProductId: string
): Promise<{ status: string }> {
  return request(`/api/matching/${uploadId}/reject`, {
    method: "POST",
    body: JSON.stringify({ destination_product_id: destinationProductId }),
  });
}

export function manualSearch(uploadId: string, query: string): Promise<Candidate[]> {
  return request(`/api/matching/${uploadId}/manual-search`, {
    method: "POST",
    body: JSON.stringify({ query, top_k: 10 }),
  });
}

export function runAutoMatch(uploadId: string): Promise<AutoMatchResult> {
  return request(`/api/matching/${uploadId}/auto-match`, { method: "POST" });
}

export function runPrioritize(uploadId: string): Promise<PrioritizeResult> {
  return request(`/api/matching/${uploadId}/prioritize`, { method: "POST" });
}

// --- Catalog management tab (HANDOFF.md section 13) -------------------------

export interface MasterProductRow {
  id: string;
  upload_id: string;
  source_row: number;
  external_id: string | null;
  product_name: string | null;
  description: string | null;
  unit: string | null;
  price: number | null;
  is_group_header: boolean;
  is_active: boolean;
  dim_w_mm: number | null;
  dim_h_mm: number | null;
  dim_d_mm: number | null;
  material: string | null;
  unit_normalized: string | null;
  created_at: string;
}

export interface MasterProductListResponse {
  items: MasterProductRow[];
  total: number;
  limit: number;
  offset: number;
  catalog_version_id: string | null;
  catalog_version_name: string | null;
}

export interface MasterProductUpdatePayload {
  external_id?: string | null;
  product_name?: string | null;
  description?: string | null;
  unit?: string | null;
  price?: number | null;
  material?: string | null;
  dim_w_mm?: number | null;
  dim_h_mm?: number | null;
  dim_d_mm?: number | null;
  unit_normalized?: string | null;
}

export function listCatalogProducts(params: {
  q?: string;
  limit?: number;
  offset?: number;
  includeInactive?: boolean;
}): Promise<MasterProductListResponse> {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset != null) query.set("offset", String(params.offset));
  if (params.includeInactive) query.set("include_inactive", "true");
  const qs = query.toString();
  return request<MasterProductListResponse>(`/api/catalog/products${qs ? `?${qs}` : ""}`);
}

// NEXT_STEPS.md item 7: add a brand-new catalog row by hand, outside the
// normal Excel-upload path. `product_name` is the only required field -
// see MasterProductCreate's own docstring in schemas.py.
export interface MasterProductCreatePayload {
  product_name: string;
  external_id?: string | null;
  description?: string | null;
  unit?: string | null;
  price?: number | null;
  material?: string | null;
  dim_w_mm?: number | null;
  dim_h_mm?: number | null;
  dim_d_mm?: number | null;
}

export function createCatalogProduct(payload: MasterProductCreatePayload): Promise<MasterProductRow> {
  return request<MasterProductRow>("/api/catalog/products", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateCatalogProduct(
  id: string,
  payload: MasterProductUpdatePayload
): Promise<MasterProductRow> {
  return request<MasterProductRow>(`/api/catalog/products/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteCatalogProduct(id: string): Promise<MasterProductRow> {
  return request<MasterProductRow>(`/api/catalog/products/${id}`, { method: "DELETE" });
}

export function restoreCatalogProduct(id: string): Promise<MasterProductRow> {
  return request<MasterProductRow>(`/api/catalog/products/${id}/restore`, { method: "POST" });
}

// --- Incremental catalog refresh (HANDOFF.md section 15 - "April -> May") ---
// Upload a newer revision of the same catalog and merge it into the active
// CatalogVersion in place, rather than replacing it: matched rows (by
// catalog code) are updated on their existing row, new codes are added, and
// anything simply missing from the new file is left untouched. See
// app/services/catalog_merge.py for the full reasoning.

export interface CatalogMergeResult {
  catalog_version_id: string;
  updated: number;
  reactivated: number;
  inserted: number;
  unmatched_existing: number;
  total_active_products: number;
  errors: Array<Record<string, unknown>>;
}

export function updateCatalogFromFile(
  catalogVersionId: string,
  file: File
): Promise<CatalogMergeResult> {
  return uploadFile<CatalogMergeResult>(
    `/api/catalog/versions/${catalogVersionId}/update-from-file`,
    file
  );
}
