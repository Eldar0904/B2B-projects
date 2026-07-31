// API base, in priority order:
//   1. ?api=... on the iframe URL - how the Next.js app passes its own
//      NEXT_PUBLIC_API_BASE_URL down, so there is one source of truth and
//      this file needs no edit when the backend moves.
//   2. window.MATCHING_API_BASE - for opening this page standalone.
//   3. localhost:8000 - this project's default backend port.
//
// A relative '/api/v1/matching' cannot be the default here: the frontend
// is served on :3000 while the backend listens on :8000, so a relative
// URL would resolve to the Next.js server and 404.
const API = (function resolveApiBase() {
  try {
    const fromQuery = new URLSearchParams(window.location.search).get('api');
    if (fromQuery) return fromQuery.replace(/\/+$/, '');
  } catch (e) {
    /* malformed query string - fall through */
  }
  if (window.MATCHING_API_BASE) {
    return String(window.MATCHING_API_BASE).replace(/\/+$/, '');
  }
  return 'http://localhost:8000/api/v1/matching';
})();

let matchResult = null;
let reviewQueue = [];
let wizardIndex = 0;
const userChoices = new Map();

// Destination IDs the user chose to skip ("Пропустить остальные").
//
// Kept separate from `userChoices` on purpose: a skipped item is
// distinguishable from both an untouched one and an explicit "Не
// подходит". `userChoices.set(id, null)` means "I looked at this and none
// of the candidates fit" - a real decision that gets saved as no_match.
// Skipping means "decide later", which the backend deliberately does NOT
// persist, leaving the row pending for a future run (see
// standalone_matching.save_results).
const skippedItems = new Set();
let destExcelFile = null;
let catalogExcelFile = null;

// Human audit of the auto-matched bucket (HANDOFF.md sections 8 / 10.3 /
// 10.4). Items in matchResult.auto_matched (exact match, hybrid-threshold,
// or llm_confirmed) never pass through the wizard - they were previously
// only VIEWABLE via the "авто N предосмотр" button, with no way to act on
// one found to be wrong. This is what the offline check in section 10.3
// couldn't do from inside the app: it found "Магнитная рыбалка" (a toy)
// auto-matched to "Мешалка магнитная" (a lab stirrer) at 0.901 purely by
// re-running the scoring script by hand.
//
// Maps destination_id -> true for any auto-matched row a reviewer has
// flagged as wrong in the "авто" detail view. Overridden rows are saved as
// "без совпадения" (see buildSaveRows) instead of the auto-match the
// backend proposed - a deliberate, visible correction, not a silent drop.
const autoMatchOverrides = new Map();

// "Use existing catalog" (HANDOFF.md section 4). null means "upload a new
// catalog file", the pre-existing behavior; set means "reuse this
// CatalogVersion, skip ingest + reindex" - see runMatching().
let catalogVersionId = null;
let catalogVersions = [];

// "Continue a previous run" (NEXT_STEPS.md item 6, "no resume" fix, Part
// B). null means "upload a new destination file", the pre-existing
// behavior; set means "reopen this destination upload instead of
// re-ingesting" - see runMatching(). Mirrors catalogVersionId/catalogVersions
// exactly (same picker pattern, same drop-zone-disabling behavior).
let resumeDestinationUploadId = null;
let resumableDestinations = [];

// Part A of the "no resume" fix: destination_ids whose decision has
// already been written to the database via POST /decisions THIS session -
// checked by buildSaveRows() so the final /save call never re-persists
// (and duplicates) a Match/Feedback row for something already saved the
// instant it was decided. A decision that came from resuming a genuinely
// earlier session is already_persisted too (see startWizard/normalizeMatchResult) -
// tracked via each record's own `already_decided` flag instead of this set,
// since those never go through saveChoice() at all.
const persistedDecisions = new Set();

// Every in-flight POST /decisions call, so saveResults() can wait for all
// of them to actually finish before deciding what's already persisted -
// without this, a decision made a split second before clicking "Сохранить"
// could race the final save and end up written twice (once by each call).
let pendingDecisionSaves = [];

function $(id) {
  return document.getElementById(id);
}

function pct(score) {
  if (score == null) return '—';
  const value = score <= 1 ? score * 100 : score;
  return `${Math.round(value * 10) / 10}%`;
}

function showScreen(name) {
  $('screenInput').hidden = name !== 'input';
  $('screenPreview').hidden = name !== 'preview';
  $('screenWizard').hidden = name !== 'wizard';
  $('screenSummary').hidden = name !== 'summary';
  $('screenDetail').hidden = name !== 'detail';
}

function isExcelFile(file) {
  if (!file) return false;
  const name = file.name.toLowerCase();
  return name.endsWith('.xlsx') || name.endsWith('.xlsm');
}

function updateDropZone(kind, file, metaText = '') {
  const isDest = kind === 'destination';
  const zone = $(isDest ? 'destDropZone' : 'catalogDropZone');
  const nameEl = $(isDest ? 'destFileName' : 'catalogFileName');
  const metaEl = $(isDest ? 'destFileMeta' : 'catalogFileMeta');

  if (file) {
    zone.classList.add('has-file');
    nameEl.textContent = file.name;
    metaEl.textContent = metaText;
  } else {
    zone.classList.remove('has-file');
    nameEl.textContent = '';
    metaEl.textContent = '';
  }
}

function setFile(kind, file) {
  if (file && !isExcelFile(file)) {
    $('inputHint').textContent = 'Нужен файл Excel (.xlsx)';
    return;
  }

  if (kind === 'destination') {
    destExcelFile = file;
    updateDropZone('destination', file);
  } else {
    catalogExcelFile = file;
    updateDropZone('catalog', file);
  }

  $('inputHint').textContent = '';
  previewExcel();
}

function clearDropZones() {
  destExcelFile = null;
  catalogExcelFile = null;
  $('destExcelFile').value = '';
  $('catalogExcelFile').value = '';
  updateDropZone('destination', null);
  updateDropZone('catalog', null);
}

// --- "Use existing catalog" (HANDOFF.md section 4) -------------------------

async function loadCatalogVersions() {
  try {
    const res = await fetch(`${API}/catalogs`);
    if (!res.ok) return;
    const data = await res.json();
    catalogVersions = Array.isArray(data.catalogs) ? data.catalogs : [];

    const select = $('catalogVersionSelect');
    select.length = 1; // keep only the "upload new file" option
    for (const cat of catalogVersions) {
      const opt = document.createElement('option');
      opt.value = cat.id;
      opt.textContent = `${cat.name} (${cat.product_count})`;
      select.appendChild(opt);
    }
  } catch (e) {
    // Non-fatal: the picker just stays empty and the upload-a-file path
    // still works exactly as before - this is a convenience, not a
    // dependency the wizard needs to function.
  }
}

function setCatalogVersion(id) {
  catalogVersionId = id || null;
  const zone = $('catalogDropZone');

  if (!catalogVersionId) {
    zone.classList.remove('disabled');
    updateDropZone('catalog', null);
    return;
  }

  // Reusing an existing catalog means no file is ingested this run, so
  // clear out any previously chosen file to avoid ambiguity about which
  // catalog is actually being used.
  catalogExcelFile = null;
  $('catalogExcelFile').value = '';
  zone.classList.add('disabled');

  const version = catalogVersions.find((v) => v.id === catalogVersionId);
  updateDropZone(
    'catalog',
    { name: version ? version.name : 'Существующий каталог' },
    version ? `${version.product_count} товаров в каталоге` : '',
  );
}

// --- "Continue a previous run" (NEXT_STEPS.md item 6, "no resume" fix,
// Part B) - mirrors "Use existing catalog" above exactly. -------------------

async function loadResumableDestinations() {
  try {
    const res = await fetch(`${API}/destinations/resumable`);
    if (!res.ok) return;
    const data = await res.json();
    resumableDestinations = Array.isArray(data.destinations) ? data.destinations : [];

    const select = $('resumeSelect');
    select.length = 1; // keep only the "upload new file" option
    for (const d of resumableDestinations) {
      const opt = document.createElement('option');
      opt.value = d.upload_id;
      opt.textContent = `${d.filename} (осталось ${d.pending} из ${d.total})`;
      select.appendChild(opt);
    }
  } catch (e) {
    // Non-fatal, same reasoning as loadCatalogVersions: the picker just
    // stays empty and uploading a fresh file still works.
  }
}

function setResumeDestination(id) {
  resumeDestinationUploadId = id || null;
  const zone = $('destDropZone');

  if (!resumeDestinationUploadId) {
    zone.classList.remove('disabled');
    updateDropZone('destination', null);
    return;
  }

  // Reopening an existing run means no file is ingested this run, so clear
  // out any previously chosen file - same reasoning as setCatalogVersion.
  destExcelFile = null;
  $('destExcelFile').value = '';
  zone.classList.add('disabled');

  const upload = resumableDestinations.find((d) => d.upload_id === resumeDestinationUploadId);
  updateDropZone(
    'destination',
    { name: upload ? upload.filename : 'Предыдущая заявка' },
    upload ? `осталось ${upload.pending} из ${upload.total}` : '',
  );
}

function setupDropZone(zoneId, inputId, kind) {
  const zone = $(zoneId);
  const input = $(inputId);

  zone.addEventListener('click', () => input.click());

  input.addEventListener('change', (event) => {
    setFile(kind, event.target.files[0] || null);
  });

  zone.addEventListener('dragover', (event) => {
    event.preventDefault();
    zone.classList.add('drag-over');
  });

  zone.addEventListener('dragleave', () => {
    zone.classList.remove('drag-over');
  });

  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    zone.classList.remove('drag-over');
    const file = event.dataTransfer.files[0];
    setFile(kind, file || null);
  });
}

function normalizeMatchResult(data) {
  if (!data) return null;
  return {
    auto_matched: Array.isArray(data.auto_matched) ? data.auto_matched : [],
    needs_review: Array.isArray(data.needs_review) ? data.needs_review : [],
    no_match: Array.isArray(data.no_match) ? data.no_match : [],
    stats: data.stats || {},
    // Threaded through to saveResults() so every Match this run creates is
    // stamped with the catalog it was actually matched against (see
    // standalone_matching.save_results) - present whether the catalog was
    // just uploaded (a new CatalogVersion was created for it) or reused.
    catalog_version_id: data.catalog_version_id || null,
  };
}

function getSummaryCounts() {
  if (!matchResult) {
    return { auto: 0, manual: 0, noMatch: 0 };
  }
  const stats = matchResult.stats || {};
  return {
    auto: Number(stats.auto_matched ?? matchResult.auto_matched.length ?? 0),
    manual: Number(stats.needs_review ?? reviewQueue.length ?? matchResult.needs_review.length ?? 0),
    noMatch: Number(stats.no_match ?? matchResult.no_match.length ?? 0),
  };
}

function getDetailRecords(kind) {
  if (!matchResult) return [];
  if (kind === 'auto') return matchResult.auto_matched;
  if (kind === 'manual') {
    return reviewQueue.length ? reviewQueue : matchResult.needs_review;
  }
  return matchResult.no_match;
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function findCandidate(record, catalogId) {
  if (!catalogId) return null;
  return record.candidates.find((c) => c.catalog_id === catalogId) || null;
}

function findCandidateName(record, catalogId) {
  const hit = findCandidate(record, catalogId);
  return hit ? hit.product_name : (catalogId || '—');
}

function findCandidateScore(record, catalogId) {
  const hit = findCandidate(record, catalogId);
  return hit ? hit.score : null;
}

// Human-meaningful identifier for a destination row.
//
// Preference order: the file's own "Код" column, then its row number,
// then nothing. Deliberately never the internal UUID - it is 36
// characters of noise that a reviewer cannot look up anywhere.
function destinationCode(record) {
  if (record.destination_external_id) return record.destination_external_id;
  if (record.destination_source_row != null) return `стр. ${record.destination_source_row}`;
  return '';
}

function formatProductCard({ code, name, description, price, score, showPrice = false }) {
  const titleParts = [];
  if (code) titleParts.push(`<span class="detail-code">${escapeHtml(code)}</span>`);
  if (name) titleParts.push(escapeHtml(name));

  let html = `<div class="wizard-option-title">${titleParts.join(' · ')}</div>`;
  if (description) {
    html += `<div class="wizard-product-desc">${escapeHtml(description)}</div>`;
  }
  if (showPrice) {
    if (price != null && price !== '') {
      html += `<div class="wizard-product-meta wizard-product-price">Цена: ${escapeHtml(String(price))}</div>`;
    } else {
      html += '<div class="wizard-product-meta wizard-product-price wizard-product-price-missing">Цена: —</div>';
    }
  }
  if (score != null) {
    html += `<div class="wizard-product-score">${pct(score)}</div>`;
  }
  return html;
}

function formatDetailItem(record, catId, catName, score, suffix = '', extraHtml = '') {
  const destCode = destinationCode(record);
  let text = destCode
    ? `<span class="detail-code">${escapeHtml(destCode)}</span> · ${escapeHtml(record.destination_name)}`
    : escapeHtml(record.destination_name);
  if (record.destination_description) {
    text += `<div class="detail-desc">${escapeHtml(record.destination_description)}</div>`;
  }
  if (record.destination_price) {
    text += `<span class="detail-price">Цена: ${escapeHtml(record.destination_price)}</span>`;
  }

  const candidate = catId ? findCandidate(record, catId) : null;
  if (catId && catName) {
    // Show the catalog code, not the UUID that identifies the row.
    const catCode = candidate?.external_id || catId;
    text += `<div class="detail-match">→ <span class="detail-code">${escapeHtml(catCode)}</span> · ${escapeHtml(catName)}`;
    const catalogDescription = candidate?.description;
    const catalogPrice = candidate?.price;
    if (catalogDescription) {
      text += `<div class="detail-desc">${escapeHtml(catalogDescription)}</div>`;
    }
    if (catalogPrice) {
      text += `<span class="detail-price">Цена: ${escapeHtml(catalogPrice)}</span>`;
    }
    text += '</div>';
  } else if (suffix) {
    text += ` → ${suffix}`;
  }
  if (score != null) {
    text += ` — ${pct(score)}`;
  }
  // Trailing block (warnings, audit badges, action buttons) - kept as a
  // separate parameter rather than baked into `suffix` so callers that
  // don't need it (manual/no-match details) are unaffected.
  if (extraHtml) {
    text += extraHtml;
  }
  return `<li class="detail-item" data-destination-id="${escapeHtml(record.destination_id)}">${text}</li>`;
}

// A candidate's `coverage` (lexical_overlap_score - "what fraction of the
// query's IDF-weighted words does this candidate actually contain",
// UPGRADE_V2.md) is the signal that would have caught "Магнитная рыбалка"
// -> "Мешалка магнитная" (HANDOFF.md section 10.3): high fuzzy-string
// similarity from shared letters, almost no real shared words. A low
// coverage on an auto-matched item is worth a human's second look even
// though the overall score cleared the auto-match bar - this is a hint,
// not a re-classification; the item still saves as "авто" unless the
// reviewer explicitly rejects it below.
const SUSPICIOUS_COVERAGE_THRESHOLD = 0.3;

function isSuspiciousAutoMatch(candidate) {
  return !!candidate && candidate.coverage != null && candidate.coverage < SUSPICIOUS_COVERAGE_THRESHOLD;
}

function formatDestinationExtra(record) {
  const parts = [];
  if (record.destination_description) parts.push(escapeHtml(record.destination_description));
  if (record.destination_price) parts.push(`Цена: ${escapeHtml(record.destination_price)}`);
  return parts.length ? ` · ${parts.join(' · ')}` : '';
}

function formatCatalogExtra(candidate) {
  if (!candidate) return '';
  const parts = [];
  if (candidate.description) parts.push(escapeHtml(candidate.description));
  if (candidate.price) parts.push(`Цена: ${escapeHtml(candidate.price)}`);
  return parts.length ? ` · ${parts.join(' · ')}` : '';
}

function formatAutoItem(record) {
  const best = record.candidates[0];
  const code = best?.catalog_id ? `[${best.catalog_id}] ` : '';
  // Visible marker for anything auto-matched by the LLM confirmer rather
  // than an exact string match or clearing the hybrid score threshold on
  // its own - this bucket is otherwise "never audited" (HANDOFF.md section
  // 8), so an LLM-pushed match needs to stand out here more than most.
  const aiTag = record.auto_match_source === 'llm_confirmed' ? ' <strong>[AI]</strong>' : '';
  return `<li>${record.destination_id}: ${record.destination_name}${formatDestinationExtra(record)} → ${code}${best?.product_name || '—'}${formatCatalogExtra(best)} (${pct(record.score)})${aiTag}</li>`;
}

function formatReviewItem(record) {
  const best = record.candidates[0];
  return `<li>${record.destination_id}: ${record.destination_name}${formatDestinationExtra(record)} — лучший: ${best?.catalog_id ? `[${best.catalog_id}] ` : ''}${best?.product_name || '—'}${formatCatalogExtra(best)} (${pct(record.score)})</li>`;
}

function formatNoMatchItem(record) {
  return `<li>${record.destination_id}: ${record.destination_name}${record.score ? ` (${pct(record.score)})` : ''}</li>`;
}

// "No resume" fix, Part A (NEXT_STEPS.md item 6): persist this ONE
// decision to the database immediately, instead of waiting for the final
// "Save & Export" click. Fire-and-forget from the caller's perspective
// (saveChoice() does not await this - a slow/failed network call must
// never block the reviewer from moving to the next item), but the promise
// itself is tracked in pendingDecisionSaves so saveResults() can wait for
// all of them before deciding what's already persisted.
//
// Deliberately minimal: only the fields save_results() actually reads
// (destination_id, catalog_id, score, decision, auto_match_source) - the
// display-only fields buildSaveRow() also fills in (name/description/
// price/etc.) exist purely for the export workbook, which this endpoint
// never builds.
async function persistDecision(record, catalogId) {
  const chosen = catalogId || null;
  const score = chosen ? (findCandidateScore(record, chosen) ?? record.score) : record.score;
  const row = {
    destination_id: record.destination_id,
    catalog_id: chosen,
    score,
    decision: chosen ? 'вручную' : 'отклонено',
    auto_match_source: null,
  };

  const promise = fetch(`${API}/decisions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows: [row], catalog_version_id: matchResult ? matchResult.catalog_version_id : null }),
  })
    .then((res) => {
      if (res.ok) {
        persistedDecisions.add(record.destination_id);
      }
      // A non-OK response is NOT retried and NOT surfaced as an error here -
      // fail open, same as every other optional-persistence path in this
      // project (LLM confirmer, embedding provider). The final /save call
      // at the end of the session is the safety net: anything not in
      // persistedDecisions by then just gets written there instead, per
      // buildSaveRows()'s own already_persisted logic.
    })
    .catch(() => {
      // Network failure - same fail-open reasoning as above.
    });

  pendingDecisionSaves.push(promise);
}

function saveChoice(record, catalogId) {
  userChoices.set(record.destination_id, catalogId || null);
  // A real decision overrides a prior skip: if the user goes back and
  // picks something, the item must stop being reported as "Пропущено".
  skippedItems.delete(record.destination_id);
  persistDecision(record, catalogId);
}

function goNext() {
  if (wizardIndex < reviewQueue.length - 1) {
    wizardIndex += 1;
    renderWizardQuestion();
    return;
  }
  renderSummary();
}

function goBack() {
  if (wizardIndex > 0) {
    wizardIndex -= 1;
    renderWizardQuestion();
  }
}

function renderPreview() {
  showScreen('preview');

  const stats = matchResult.stats;
  $('previewStats').textContent =
    `Всего: ${stats.total}, авто: ${stats.auto_matched}, на проверку: ${stats.needs_review}, без пары: ${stats.no_match}`;

  $('previewAuto').innerHTML = matchResult.auto_matched.map(formatAutoItem).join('') || '<li>—</li>';
  $('previewReview').innerHTML = matchResult.needs_review.map(formatReviewItem).join('') || '<li>—</li>';
  $('previewNoMatch').innerHTML = matchResult.no_match.map(formatNoMatchItem).join('') || '<li>—</li>';

  const reviewCount = matchResult.needs_review.length;
  $('previewContinueBtn').textContent = reviewCount
    ? `Ручная проверка (${reviewCount})`
    : 'К итогу';
}

function renderWizardQuestion() {
  const record = reviewQueue[wizardIndex];
  if (!record) {
    renderSummary();
    return;
  }

  showScreen('wizard');
  $('wizardProgress').textContent = `${wizardIndex + 1} из ${reviewQueue.length}`;
  $('destLabel').innerHTML = formatProductCard({
    // Real catalog code ("Код" column), not the internal UUID. Falling
    // back to the row number keeps the item locatable in the source
    // spreadsheet; destination files in this project usually have the
    // code column empty, and a bare 36-character UUID on screen is noise
    // a reviewer cannot act on.
    code: destinationCode(record),
    name: record.destination_name,
    description: record.destination_description,
    price: record.destination_price,
    showPrice: true,
  });
  $('wizardBackBtn').disabled = wizardIndex === 0;

  const chosen = userChoices.get(record.destination_id);
  const container = $('candidateButtons');
  container.innerHTML = '';

  record.candidates.forEach((candidate) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'wizard-option';
    if (chosen === candidate.catalog_id) {
      btn.classList.add('selected');
    }
    btn.innerHTML = formatProductCard({
      // external_id is the human-meaningful catalog code
      // (e.g. 521-101-0131-0001); catalog_id is an internal UUID.
      code: candidate.external_id || candidate.catalog_id,
      name: candidate.product_name,
      description: candidate.description,
      price: candidate.price,
      score: candidate.score,
      showPrice: true,
    });
    btn.addEventListener('click', () => {
      saveChoice(record, candidate.catalog_id);
      goNext();
    });
    container.appendChild(btn);
  });

  const noneBtn = document.createElement('button');
  noneBtn.type = 'button';
  noneBtn.className = 'wizard-option';
  if (chosen === null && userChoices.has(record.destination_id)) {
    noneBtn.classList.add('selected');
  }
  noneBtn.textContent = 'Не подходит';
  noneBtn.addEventListener('click', () => {
    saveChoice(record, null);
    goNext();
  });
  container.appendChild(noneBtn);

  const skipBtn = document.createElement('button');
  skipBtn.type = 'button';
  skipBtn.className = 'wizard-option wizard-option-skip';
  if (skippedItems.has(record.destination_id)) {
    skipBtn.classList.add('selected');
  }
  skipBtn.textContent = 'Пропустить остальные';
  skipBtn.addEventListener('click', () => {
    // Skips this item and every remaining one, then jumps to the summary.
    //
    // Only items with no decision yet are skipped: anything the user
    // already answered keeps its answer, so pressing this at position 40
    // of 200 does not throw away the first 39 decisions. Going back and
    // choosing something later clears that item's skip (see saveChoice).
    for (let i = wizardIndex; i < reviewQueue.length; i += 1) {
      const id = reviewQueue[i].destination_id;
      if (userChoices.get(id) === undefined) {
        skippedItems.add(id);
      }
    }
    renderSummary();
  });
  container.appendChild(skipBtn);
}

function startWizard() {
  if (!matchResult) return;
  reviewQueue = [...(matchResult.needs_review || [])];
  wizardIndex = 0;
  userChoices.clear();
  // Must be cleared alongside userChoices, or skips/overrides from a
  // previous run would leak into this one and silently mis-tag fresh items.
  skippedItems.clear();
  autoMatchOverrides.clear();
  // Same reasoning: a stale persisted-decisions set from a previous run in
  // this same page load would wrongly mark fresh items as already_persisted.
  persistedDecisions.clear();
  pendingDecisionSaves = [];

  if (reviewQueue.length === 0) {
    renderSummary();
    return;
  }

  renderWizardQuestion();
}

function renderSummary() {
  if (!matchResult) return;
  showScreen('summary');

  const counts = getSummaryCounts();
  // Surfaces any audit overrides right on the summary screen too, not just
  // inside the "авто" detail view itself - a reviewer who rejected a match
  // and went back should still see that it "took" without re-opening the
  // detail view to check.
  const autoLabel = autoMatchOverrides.size
    ? `авто ${counts.auto} (отклонено: ${autoMatchOverrides.size}) предосмотр`
    : `авто ${counts.auto} предосмотр`;
  $('autoPreviewBtn').textContent = autoLabel;
  $('manualPreviewBtn').textContent = `вручную ${counts.manual} предосмотр`;
  $('noMatchPreviewBtn').textContent = `без совпадения ${counts.noMatch} предосмотр`;
  $('saveHint').textContent = '';
}

function renderDetail(kind) {
  if (!matchResult) return;
  showScreen('detail');

  const records = getDetailRecords(kind);
  const counts = getSummaryCounts();
  let title = '';
  let itemsHtml = '';

  if (kind === 'auto') {
    const overriddenCount = records.filter((r) => autoMatchOverrides.has(r.destination_id)).length;
    title = overriddenCount ? `Авто ${counts.auto} (отклонено: ${overriddenCount})` : `Авто ${counts.auto}`;
    itemsHtml = records
      .map((record) => {
        const best = record.candidates[0];
        // Same [AI] marker as the preview list (formatAutoItem) - a match
        // pushed through by the LLM confirmer must stand out here too,
        // since this "auto" bucket is otherwise the one nobody re-checks.
        // [продолжено] marks a decision that came from resuming an earlier
        // session (NEXT_STEPS.md item 6, Part B) rather than this run's own
        // classification - a reviewer should know the difference, since
        // this one was a real human choice, not the engine's guess.
        const resumedTag = record.already_decided ? ' [продолжено]' : '';
        const name = record.auto_match_source === 'llm_confirmed'
          ? `${best?.product_name || ''} [AI]${resumedTag}`
          : `${best?.product_name || ''}${resumedTag}`;

        const rejected = autoMatchOverrides.has(record.destination_id);
        let extraHtml = '';
        if (rejected) {
          extraHtml += ' <span class="detail-rejected">ОТКЛОНЕНО ПРОВЕРЯЮЩИМ - будет сохранено как «без совпадения»</span>';
        } else if (record.already_decided) {
          extraHtml += ` <span class="detail-resumed">решено ранее (${escapeHtml(record.resumed_decision)})</span>`;
        } else if (isSuspiciousAutoMatch(best)) {
          // HANDOFF.md section 10.3's exact failure mode: high overall
          // score, low real word overlap - flagged, not blocked, since
          // most of these are still correct (e.g. exact matches always
          // report coverage 1.0 and never hit this branch).
          extraHtml += ' <span class="detail-warn">⚠ низкое совпадение слов — проверьте вручную</span>';
        }
        extraHtml += '<div class="detail-actions-row">';
        extraHtml += rejected
          ? `<button type="button" class="detail-action detail-undo" data-action="undo-auto-reject">Вернуть в «авто»</button>`
          : `<button type="button" class="detail-action detail-reject" data-action="reject-auto-match">Отклонить (нет совпадения)</button>`;
        extraHtml += '</div>';

        return formatDetailItem(
          record,
          best?.catalog_id,
          name,
          best?.score ?? record.score,
          '',
          extraHtml,
        );
      })
      .join('');
  } else if (kind === 'manual') {
    title = `Вручную ${counts.manual}`;
    itemsHtml = records
      .map((record) => {
        // Skipped items are listed as "Пропущено" rather than silently
        // collapsing into "Не подходит" - the two mean different things
        // and only one of them gets written to the database.
        if (skippedItems.has(record.destination_id)) {
          return formatDetailItem(record, null, null, record.score, 'Пропущено');
        }
        const chosen = userChoices.get(record.destination_id);
        if (chosen) {
          return formatDetailItem(
            record,
            chosen,
            findCandidateName(record, chosen),
            findCandidateScore(record, chosen) ?? record.score,
          );
        }
        return formatDetailItem(
          record,
          null,
          null,
          record.score,
          'Не подходит',
        );
      })
      .join('');
  } else {
    title = `Без совпадения ${counts.noMatch}`;
    itemsHtml = records
      .map((record) => formatDetailItem(
        record,
        null,
        null,
        record.score,
        '',
        // [продолжено] - same reasoning as the "auto" detail view above:
        // this specific "no match" decision came from resuming an earlier
        // session, not this run's own classification.
        record.already_decided ? ' <span class="detail-resumed">[продолжено] решено ранее</span>' : '',
      ))
      .join('');
  }

  $('detailTitle').textContent = title;
  $('detailList').innerHTML = itemsHtml || '<li class="detail-item">—</li>';
}

async function previewExcel() {
  const form = buildExcelFormData(false);
  if (!form) {
    if (destExcelFile) updateDropZone('destination', destExcelFile);
    if (catalogExcelFile) updateDropZone('catalog', catalogExcelFile);
    return;
  }

  const res = await fetch(`${API}/excel/preview`, { method: 'POST', body: form });
  if (!res.ok) {
    $('inputHint').textContent = 'Не удалось прочитать Excel';
    return;
  }

  const payload = await res.json();
  if (destExcelFile) {
    updateDropZone('destination', destExcelFile, `${payload.destination_count} строк`);
  }
  if (catalogExcelFile) {
    updateDropZone('catalog', catalogExcelFile, `${payload.catalog_count} строк`);
  }
}

function buildExcelFormData(requireBothSheets) {
  if (requireBothSheets && (!destExcelFile || !catalogExcelFile)) return null;
  if (!destExcelFile && !catalogExcelFile) return null;

  const form = new FormData();
  if (destExcelFile) form.append('destination_file', destExcelFile);
  if (catalogExcelFile) form.append('catalog_file', catalogExcelFile);
  return form;
}

function setMatchProgress(visible, percent = 0, current = 0, total = 0) {
  $('matchProgressBox').hidden = !visible;
  $('matchProgressBar').value = percent;
  $('matchProgressText').textContent = total
    ? `${percent}% · ${current}/${total}`
    : `${percent}% · 0/0`;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollMatchingJob(jobId) {
  while (true) {
    const res = await fetch(`${API}/jobs/${jobId}`);
    if (!res.ok) {
      throw new Error('Не удалось получить статус подбора');
    }
    const data = await res.json();
    setMatchProgress(true, data.percent, data.current, data.total);

    if (data.status === 'done') {
      if (!data.result) {
        await sleep(200);
        continue;
      }
      return normalizeMatchResult(data.result);
    }
    if (data.status === 'error') {
      throw new Error(data.error || 'Ошибка подбора');
    }
    await sleep(300);
  }
}

async function runMatching() {
  if (!destExcelFile && !resumeDestinationUploadId) {
    $('inputHint').textContent = 'Загрузите файл заявки или выберите предыдущий подбор для продолжения.';
    return;
  }
  if (!catalogExcelFile && !catalogVersionId) {
    $('inputHint').textContent = 'Загрузите файл каталога или выберите существующий.';
    return;
  }

  const excelForm = new FormData();
  // A file is only attached when NOT reopening an existing destination
  // upload - sending both would be ambiguous about which run this actually
  // continues, so resumeDestinationUploadId (a query param, see below)
  // always wins. Mirrors catalog_version_id/catalogExcelFile exactly below.
  if (!resumeDestinationUploadId && destExcelFile) {
    excelForm.append('destination_file', destExcelFile);
  }
  // A file is only attached when NOT reusing an existing catalog version -
  // sending both would be ambiguous about which catalog this run actually
  // means, so catalog_version_id (a query param, see below) always wins.
  if (!catalogVersionId && catalogExcelFile) {
    excelForm.append('catalog_file', catalogExcelFile);
  }

  $('runMatchBtn').disabled = true;
  $('inputHint').textContent = '';
  setMatchProgress(true, 0, 0, 0);

  try {
    const params = new URLSearchParams();
    if (catalogVersionId) params.set('catalog_version_id', catalogVersionId);
    if (resumeDestinationUploadId) params.set('destination_upload_id', resumeDestinationUploadId);
    const qs = params.toString();
    const startUrl = qs ? `${API}/excel/run/start?${qs}` : `${API}/excel/run/start`;
    const startRes = await fetch(startUrl, {
      method: 'POST',
      body: excelForm,
    });
    if (!startRes.ok) {
      const errText = await startRes.text();
      throw new Error(errText);
    }
    const { job_id: jobId, total } = await startRes.json();
    setMatchProgress(true, 0, 0, total);

    matchResult = await pollMatchingJob(jobId);
    if (!matchResult) {
      throw new Error('Пустой результат подбора');
    }
    setMatchProgress(false);
    startWizard();
  } catch (err) {
    setMatchProgress(false);
    $('inputHint').textContent = `Ошибка: ${err.message}`;
  } finally {
    $('runMatchBtn').disabled = false;
  }
}

async function saveResults() {
  if (!matchResult) return;

  // No longer blocks the download when some manual-review items are
  // still undecided - a reviewer must be able to export whatever progress
  // exists at any point, not just after every single item has an answer.
  // Any item without an explicit decision (skipped via "Пропустить
  // остальные", or simply never opened in the wizard) is exported as
  // "пропущено" by buildSaveRows() below - the same "decide later, don't
  // persist a fake decision" treatment skipped items already got, just
  // no longer gated behind finishing the whole queue first.
  const undecidedCount = reviewQueue.filter(
    (record) => !userChoices.has(record.destination_id) && !skippedItems.has(record.destination_id),
  ).length;
  const undecidedNote = undecidedCount
    ? ` Не проверено позиций: ${undecidedCount} (сохранены как «пропущено» - вернутся при следующем подборе).`
    : '';

  // Wait for every in-flight POST /decisions call to actually resolve
  // before deciding what's already persisted - otherwise a decision made a
  // split second before clicking "Сохранить" could race the final save and
  // get written twice (see persistDecision's own comment).
  await Promise.allSettled(pendingDecisionSaves);
  const rows = buildSaveRows();

  try {
    const res = await fetch(`${API}/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rows, catalog_version_id: matchResult.catalog_version_id || null }),
    });

    if (!res.ok) {
      $('saveHint').textContent = 'Ошибка сохранения';
      return;
    }

    const blob = await res.blob();
    const disposition = res.headers.get('Content-Disposition') || '';
    const filenameMatch = disposition.match(/filename=\"?([^\";]+)\"?/);
    const filename = filenameMatch ? filenameMatch[1] : 'matching.xlsx';

    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);

    $('saveHint').textContent = `Сохранено: ${rows.length} строк → ${filename}.${undecidedNote}`;
  } catch (err) {
    $('saveHint').textContent = `Ошибка: ${err.message}`;
  }
}

function buildSaveRow(record, catalogId, catalogName, score, decision, autoMatchSource = null, alreadyPersisted = false) {
  const candidate = catalogId ? findCandidate(record, catalogId) : null;
  return {
    destination_id: record.destination_id,
    destination_name: record.destination_name,
    destination_description: record.destination_description || null,
    destination_price: record.destination_price || null,
    catalog_id: catalogId || null,
    // The catalog's own code ("Код"), separate from catalog_id (a UUID).
    // The export puts this in the "Код (каталог)" column - it is the value
    // a procurement officer actually quotes.
    catalog_code: candidate?.external_id || null,
    catalog_name: catalogName || null,
    catalog_description: candidate?.description || null,
    catalog_price: candidate?.price || null,
    score,
    decision,
    // "exact_match" / "hybrid_threshold" / "llm_confirmed" for an "авто"
    // row (see standalone_matching.py's _classify_destination_product) -
    // null for every other decision. Threaded through to /save so
    // save_results can stamp a distinct, traceable Match.method instead of
    // collapsing every auto-match into one indistinguishable label.
    auto_match_source: autoMatchSource,
    // "No resume" fix (NEXT_STEPS.md item 6): true for anything already
    // written to the database - either via POST /decisions the instant it
    // was decided this session (persistedDecisions), or because it was
    // already decided in a genuinely earlier session before this one even
    // started (record.already_decided). save_results skips these entirely
    // rather than inserting a second, redundant Match/Feedback row for the
    // same decision every time "Сохранить" is clicked.
    already_persisted: alreadyPersisted,
  };
}

function buildSaveRows() {
  const rows = [];

  for (const record of matchResult.auto_matched) {
    const best = record.candidates[0];
    if (autoMatchOverrides.has(record.destination_id)) {
      // A human audited this specific auto-match (the "авто" detail view -
      // HANDOFF.md sections 8/10.3/10.4) and rejected it - record that
      // decision as a real "без совпадения" outcome instead of silently
      // keeping the auto-match the backend proposed. This is exactly the
      // control that would have caught "Магнитная рыбалка" ->
      // "Мешалка магнитная" (0.901, genuinely different products) before
      // it reached an export file. A fresh decision made just now, at save
      // time - never already persisted.
      rows.push(buildSaveRow(record, null, null, record.score, 'без совпадения'));
      continue;
    }
    if (record.already_decided) {
      // Resumed from a genuinely earlier session (Part B) - report the
      // real decision that was already made, and never re-write it.
      rows.push(buildSaveRow(
        record,
        record.selected_catalog_id,
        best?.product_name || null,
        record.score,
        record.resumed_decision,
        null,
        true,
      ));
      continue;
    }
    rows.push(buildSaveRow(
      record,
      record.selected_catalog_id,
      best?.product_name || null,
      best?.score ?? record.score,
      'авто',
      record.auto_match_source || null,
    ));
  }

  for (const record of reviewQueue) {
    // "пропущено" must be sent as its own decision, not folded into
    // "отклонено". The backend treats them very differently: отклонено is
    // persisted as a no_match verdict, while пропущено is deliberately
    // not written at all, leaving the row pending so it comes back on the
    // next run. Collapsing the two would permanently record a decision
    // the user explicitly declined to make.
    //
    // Two cases now get "пропущено", not just an explicit skip: a record
    // explicitly skipped via "Пропустить остальные", AND a record that was
    // simply never opened in the wizard at all (userChoices has no entry
    // for it - `.has()`, not a truthiness check on `.get()`, since an
    // explicit "Не подходит" click stores `null`, which is just as falsy
    // as "never visited" and must NOT be confused with it - only `.has()`
    // tells them apart). This is what lets saveResults() export progress
    // at any point instead of requiring every single item to be decided
    // first - an unvisited item is "not decided yet", not "rejected".
    const visited = userChoices.has(record.destination_id);
    if (skippedItems.has(record.destination_id) || !visited) {
      rows.push(buildSaveRow(record, null, null, record.score, 'пропущено'));
      continue;
    }
    const chosen = userChoices.get(record.destination_id);
    // Already written via POST /decisions the instant this was decided
    // (saveChoice() -> persistDecision()) - saveResults() awaits every
    // in-flight save first, so persistedDecisions is fully up to date by
    // the time this runs. Only a failed/never-completed incremental save
    // falls through to being written here instead, same fail-open shape as
    // every other optional-provider fallback in this project.
    const alreadyPersisted = persistedDecisions.has(record.destination_id);
    rows.push(buildSaveRow(
      record,
      chosen || null,
      chosen ? findCandidateName(record, chosen) : null,
      chosen ? findCandidateScore(record, chosen) ?? record.score : record.score,
      chosen ? 'вручную' : 'отклонено',
      null,
      alreadyPersisted,
    ));
  }

  for (const record of matchResult.no_match) {
    if (record.already_decided) {
      // Resumed from a genuinely earlier session (Part B) - see the
      // auto_matched loop above for the same reasoning.
      rows.push(buildSaveRow(record, null, null, record.score, record.resumed_decision, null, true));
      continue;
    }
    rows.push(buildSaveRow(
      record,
      null,
      null,
      record.score,
      'без совпадения',
    ));
  }

  return rows;
}

document.addEventListener('DOMContentLoaded', () => {
  setupDropZone('destDropZone', 'destExcelFile', 'destination');
  setupDropZone('catalogDropZone', 'catalogExcelFile', 'catalog');

  loadCatalogVersions();
  $('catalogVersionSelect').addEventListener('change', (event) => {
    setCatalogVersion(event.target.value);
  });

  loadResumableDestinations();
  $('resumeSelect').addEventListener('change', (event) => {
    setResumeDestination(event.target.value);
  });

  $('runMatchBtn').addEventListener('click', runMatching);
  $('previewContinueBtn').addEventListener('click', startWizard);
  $('previewBackBtn').addEventListener('click', () => showScreen('input'));
  $('wizardBackBtn').addEventListener('click', goBack);
  $('saveBtn').addEventListener('click', saveResults);
  $('autoPreviewBtn').addEventListener('click', () => renderDetail('auto'));
  $('manualPreviewBtn').addEventListener('click', () => renderDetail('manual'));
  $('noMatchPreviewBtn').addEventListener('click', () => renderDetail('no_match'));
  $('detailBackBtn').addEventListener('click', () => renderSummary());

  // Delegated (not one listener per row) since detailList's contents are
  // rebuilt via innerHTML on every renderDetail() call - listeners attached
  // directly to those <li> elements would be destroyed and silently stop
  // firing the moment the list re-renders (e.g. after the reject/undo
  // click below causes its own re-render). Only "auto" detail items ever
  // carry a data-action button, so this is a no-op on the manual/no-match
  // detail views.
  $('detailList').addEventListener('click', (event) => {
    const btn = event.target.closest('button[data-action]');
    if (!btn) return;
    const li = btn.closest('li[data-destination-id]');
    const destinationId = li ? li.dataset.destinationId : null;
    if (!destinationId) return;

    if (btn.dataset.action === 'reject-auto-match') {
      autoMatchOverrides.set(destinationId, true);
    } else if (btn.dataset.action === 'undo-auto-reject') {
      autoMatchOverrides.delete(destinationId);
    }
    renderDetail('auto');
  });
});
