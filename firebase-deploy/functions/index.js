/**
 * Callable: searchSuppliers
 *
 * Setup (one-time, requires Blaze plan):
 *   1. Google Custom Search: https://programmablesearchengine.google.com/
 *      - Create search engine (search entire web)
 *      - Enable Custom Search API in Google Cloud Console
 *      - Copy API key + Search engine ID (cx)
 *   2. Anthropic: https://console.anthropic.com/ → API key
 *   3. Set secrets:
 *        firebase functions:secrets:set GOOGLE_CSE_API_KEY
 *        firebase functions:secrets:set GOOGLE_CSE_ID
 *        firebase functions:secrets:set ANTHROPIC_API_KEY
 *   4. Deploy:
 *        cd firebase-deploy && npm install --prefix functions
 *        firebase deploy --only functions
 */

const { initializeApp } = require("firebase-admin/app");
const { onCall, HttpsError } = require("firebase-functions/v2/https");
const { defineSecret } = require("firebase-functions/params");

initializeApp();

const googleCseKey = defineSecret("GOOGLE_CSE_API_KEY");
const googleCseId = defineSecret("GOOGLE_CSE_ID");
const anthropicKey = defineSecret("ANTHROPIC_API_KEY");

const MAX_ITEMS = 8;
const CSE_RESULTS = 8;
const ANTHROPIC_MODEL = "claude-3-5-haiku-latest";

function cleanItemLine(line) {
  return String(line || "")
    .replace(/\sx?\d+\s*$/i, "")
    .trim();
}

function buildSearchQuery(item, region) {
  const product = cleanItemLine(item);
  const loc = region || "Казахстан";
  return `${product} поставщик ${loc} купить оптом`;
}

async function googleCustomSearch(apiKey, cx, query) {
  const url = new URL("https://www.googleapis.com/customsearch/v1");
  url.searchParams.set("key", apiKey);
  url.searchParams.set("cx", cx);
  url.searchParams.set("q", query);
  url.searchParams.set("num", String(Math.min(CSE_RESULTS, 10)));
  url.searchParams.set("lr", "lang_ru|lang_kk");

  const res = await fetch(url.toString());
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Google CSE ${res.status}: ${body.slice(0, 200)}`);
  }
  const data = await res.json();
  return (data.items || []).map((item) => ({
    title: item.title || "",
    link: item.link || "",
    snippet: item.snippet || "",
    displayLink: item.displayLink || "",
  }));
}

function extractJsonArray(text) {
  const trimmed = String(text || "").trim();
  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const raw = fenced ? fenced[1].trim() : trimmed;
  const start = raw.indexOf("[");
  const end = raw.lastIndexOf("]");
  if (start === -1 || end === -1) return [];
  return JSON.parse(raw.slice(start, end + 1));
}

function normalizeSupplier(row, index) {
  const rating = Number(row.rating);
  return {
    name: String(row.name || "—").slice(0, 120),
    loc: String(row.loc || "—").slice(0, 120),
    price: String(row.price || "Цена по запросу").slice(0, 80),
    lead: String(row.lead || "—").slice(0, 80),
    moq: String(row.moq || "—").slice(0, 80),
    rating: Number.isFinite(rating) ? Math.min(5, Math.max(1, rating)) : 4.0,
    best: Boolean(row.best),
    src: String(row.src || row.url || "web").slice(0, 80),
    url: String(row.url || "").slice(0, 500),
  };
}

function ensureOneBest(suppliers) {
  if (!suppliers.length) return suppliers;
  const hasBest = suppliers.some((s) => s.best);
  if (!hasBest) suppliers[0].best = true;
  return suppliers;
}

async function rankWithClaude(apiKey, item, region, hits) {
  if (!hits.length) return [];

  const prompt = `Ты помощник закупщика B2B Fitout (оснащение школ и кабинетов).

Позиция: ${item}
Регион: ${region || "Казахстан / Центральная Азия"}

Результаты веб-поиска (JSON):
${JSON.stringify(hits, null, 2)}

Из этих результатов извлеки до 3 реальных поставщиков или дистрибьюторов.
Используй только информацию из сниппетов — не выдумывай компании.
Если цены нет — укажи "Цена по запросу".
Одному лучшему поставщику поставь "best": true.

Верни ТОЛЬКО JSON-массив без пояснений:
[{"name":"...","loc":"...","price":"...","lead":"...","moq":"...","rating":4.2,"best":false,"src":"domain.kz","url":"https://..."}]`;

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: ANTHROPIC_MODEL,
      max_tokens: 1200,
      messages: [{ role: "user", content: prompt }],
    }),
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Anthropic ${res.status}: ${body.slice(0, 200)}`);
  }

  const data = await res.json();
  const text = (data.content || [])
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("\n");

  let parsed;
  try {
    parsed = extractJsonArray(text);
  } catch (e) {
    console.warn("Claude JSON parse failed for item:", item, text.slice(0, 300));
    return [];
  }

  return ensureOneBest(
    parsed.slice(0, 3).map((row, i) => normalizeSupplier(row, i))
  );
}

async function searchOneItem(cseKey, cseId, anthropic, item, region) {
  const query = buildSearchQuery(item, region);
  const hits = await googleCustomSearch(cseKey, cseId, query);
  const suppliers = await rankWithClaude(anthropic, item, region, hits);
  return { item, query, suppliers, hitCount: hits.length };
}

exports.searchSuppliers = onCall(
  {
    region: "asia-southeast1",
    timeoutSeconds: 120,
    memory: "512MiB",
    secrets: [googleCseKey, googleCseId, anthropicKey],
  },
  async (request) => {
    if (!request.auth) {
      throw new HttpsError("unauthenticated", "Войдите в аккаунт для поиска поставщиков.");
    }

    const cseKey = googleCseKey.value();
    const cseId = googleCseId.value();
    const anthropic = anthropicKey.value();

    if (!cseKey || !cseId || !anthropic) {
      throw new HttpsError(
        "failed-precondition",
        "API-ключи не настроены. Задайте GOOGLE_CSE_API_KEY, GOOGLE_CSE_ID и ANTHROPIC_API_KEY."
      );
    }

    const items = Array.isArray(request.data?.items)
      ? request.data.items.map((s) => String(s).trim()).filter(Boolean)
      : [];
    const region = String(request.data?.region || "Казахстан").trim();

    if (!items.length) {
      throw new HttpsError("invalid-argument", "Передайте хотя бы одну позицию в items.");
    }
    if (items.length > MAX_ITEMS) {
      throw new HttpsError(
        "invalid-argument",
        `Максимум ${MAX_ITEMS} позиций за один запуск (лимит Google CSE).`
      );
    }

    const results = [];
    for (const item of items) {
      try {
        results.push(await searchOneItem(cseKey, cseId, anthropic, item, region));
      } catch (err) {
        console.error("searchOneItem failed:", item, err);
        results.push({
          item,
          query: buildSearchQuery(item, region),
          suppliers: [],
          hitCount: 0,
          error: err.message || String(err),
        });
      }
    }

    return {
      mode: "live",
      region,
      results,
      queriesUsed: results.length,
      warning:
        items.length > 5
          ? "Большой список — учитывайте лимит Google CSE (~100 запросов/день бесплатно)."
          : null,
    };
  }
);
