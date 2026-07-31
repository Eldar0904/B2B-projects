"use client";

import { useCallback, useEffect, useState } from "react";
import {
  FeedbackStats,
  TrainResult,
  TrainingReadiness,
  UploadInfo,
  getFeedbackStats,
  getTrainingReadiness,
  listUploads,
  trainModel,
} from "@/lib/api";

const LAST_UPLOAD_ID_KEY = "product-matching:last-destination-upload-id";

function StatRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between border-b border-gray-100 py-1.5 text-sm last:border-0">
      <span className="text-gray-600">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  );
}

export default function StatsTab() {
  const [uploadId, setUploadId] = useState("");
  const [recentUploads, setRecentUploads] = useState<UploadInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [stats, setStats] = useState<FeedbackStats | null>(null);
  const [readiness, setReadiness] = useState<TrainingReadiness | null>(null);

  const [training, setTraining] = useState(false);
  const [trainError, setTrainError] = useState<string | null>(null);
  const [trainResult, setTrainResult] = useState<TrainResult | null>(null);

  useEffect(() => {
    const saved = window.localStorage.getItem(LAST_UPLOAD_ID_KEY);
    if (saved) setUploadId(saved);
    listUploads("destination")
      .then(setRecentUploads)
      .catch(() => {
        /* non-fatal: the id field still works */
      });
  }, []);

  const loadStats = useCallback(async (idOverride?: string) => {
    const id = (idOverride ?? uploadId).trim();
    setLoading(true);
    setError(null);
    setStats(null);
    setReadiness(null);
    setTrainResult(null);
    setTrainError(null);
    try {
      // feedback-stats is per-upload; training-readiness is global (all
      // feedback across uploads) but accepts an optional upload_id filter.
      const [s, r] = await Promise.all([
        id ? getFeedbackStats(id) : Promise.resolve(null),
        getTrainingReadiness(id || undefined),
      ]);
      setStats(s);
      setReadiness(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load stats");
    } finally {
      setLoading(false);
    }
  }, [uploadId]);

  const handleTrain = useCallback(async () => {
    setTraining(true);
    setTrainError(null);
    setTrainResult(null);
    try {
      const result = await trainModel(uploadId.trim() || undefined);
      setTrainResult(result);
      // refresh readiness afterward in case the picture changed
      const r = await getTrainingReadiness(uploadId.trim() || undefined);
      setReadiness(r);
    } catch (e) {
      setTrainError(e instanceof Error ? e.message : "Training failed");
    } finally {
      setTraining(false);
    }
  }, [uploadId]);

  const readinessPct =
    readiness && readiness.min_required > 0
      ? Math.min(100, Math.round((readiness.total_examples / readiness.min_required) * 100))
      : 0;

  return (
    <>
      <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <label className="mb-2 block text-sm font-medium text-gray-700">
          Destination upload ID
        </label>
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm"
            placeholder="Leave blank to see global training readiness across all uploads"
            value={uploadId}
            onChange={(e) => setUploadId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && loadStats()}
          />
          <button
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            onClick={() => loadStats()}
            disabled={loading}
          >
            {loading ? "Loading..." : "Load"}
          </button>
        </div>
        {recentUploads.length > 0 && (
          <div className="mt-3">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
              Recent uploads
            </p>
            <div className="flex flex-wrap gap-1.5">
              {recentUploads.map((u) => (
                <button
                  key={u.id}
                  className="rounded-full border border-gray-200 px-2.5 py-1 text-xs hover:border-blue-300 hover:bg-blue-50"
                  onClick={() => {
                    setUploadId(u.id);
                    loadStats(u.id);
                  }}
                >
                  {u.filename}
                </button>
              ))}
            </div>
          </div>
        )}
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      </div>

      {stats && (
        <div className="mt-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-gray-800">
            Feedback for this upload
          </h2>
          <StatRow label="Total decisions" value={stats.total} />
          <StatRow label="Selected from top-3" value={stats.user_selected} />
          <StatRow label="Selected via manual search" value={stats.manual_search_selected} />
          <StatRow label="Rejected (none of these)" value={stats.no_match} />
          <StatRow label="Auto-accepted" value={stats.auto_accepted} />
          <StatRow label="User rejected" value={stats.user_rejected} />
          <StatRow label="Auto-rejected" value={stats.auto_rejected} />
        </div>
      )}

      {readiness && (
        <div className="mt-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <h2 className="mb-1 text-sm font-semibold text-gray-800">
            Training readiness
            <span className="ml-2 font-normal text-gray-400">
              {uploadId.trim() ? "(this upload)" : "(all uploads)"}
            </span>
          </h2>
          <p className="mb-3 text-xs text-gray-500">
            A supervised model needs at least {readiness.min_required} labeled examples
            before training is attempted.
          </p>

          <div className="mb-2 flex items-center justify-between text-sm text-gray-600">
            <span>
              {readiness.total_examples} / {readiness.min_required} examples
            </span>
            <span>{readinessPct}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-gray-200">
            <div
              className={`h-full ${readiness.ready ? "bg-green-600" : "bg-blue-600"}`}
              style={{ width: `${readinessPct}%` }}
            />
          </div>

          <div className="mt-3 flex gap-4 text-xs text-gray-500">
            <span>Positive: {readiness.positive_examples}</span>
            <span>Negative: {readiness.negative_examples}</span>
          </div>

          {readiness.ready ? (
            <p className="mt-3 rounded-md bg-green-50 px-3 py-2 text-sm text-green-800">
              Enough data to train.
            </p>
          ) : (
            <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
              Need {readiness.examples_needed} more example
              {readiness.examples_needed === 1 ? "" : "s"}. Review more products to
              generate labeled feedback.
            </p>
          )}

          <div className="mt-4">
            <button
              className="rounded-md bg-gray-800 px-4 py-2 text-sm font-medium text-white hover:bg-gray-900 disabled:opacity-50"
              onClick={handleTrain}
              disabled={training}
              title="Build a dataset from stored feedback, train, and evaluate against the baseline"
            >
              {training ? "Training..." : "Train & evaluate model"}
            </button>
            <p className="mt-1 text-xs text-gray-400">
              Training here evaluates against the baseline; it does not change live matching.
            </p>
          </div>

          {trainError && <p className="mt-3 text-sm text-red-600">{trainError}</p>}

          {trainResult && (
            <div className="mt-4 rounded-md border border-gray-200 p-4 text-sm">
              <p className="mb-2 font-medium text-gray-800">{trainResult.reason}</p>
              <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-gray-600">
                <span>Total examples: {trainResult.n_total}</span>
                <span>Train / test: {trainResult.n_train} / {trainResult.n_test}</span>
                <span>Positive: {trainResult.n_positive}</span>
                <span>Negative: {trainResult.n_negative}</span>
                <span>
                  Baseline AUC:{" "}
                  {trainResult.baseline_auc !== null ? trainResult.baseline_auc.toFixed(3) : "—"}
                </span>
                <span>
                  Model AUC:{" "}
                  {trainResult.model_auc !== null ? trainResult.model_auc.toFixed(3) : "—"}
                </span>
                <span>
                  Improvement:{" "}
                  {trainResult.improvement !== null
                    ? `${trainResult.improvement >= 0 ? "+" : ""}${trainResult.improvement.toFixed(3)}`
                    : "—"}
                </span>
                <span
                  className={trainResult.should_deploy ? "text-green-700" : "text-gray-600"}
                >
                  Would deploy: {trainResult.should_deploy ? "yes" : "no"}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {!stats && !readiness && !loading && !error && (
        <p className="mt-4 text-sm text-gray-400">
          Load an upload above to see feedback stats and training readiness.
        </p>
      )}
    </>
  );
}
