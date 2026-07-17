/* =====================================================================
   COMPARE & COMPLETENESS TOOLS — rule-based, deterministic, no API calls.

   Pipeline mirrors catalog-matcher (Python) exactly:

     0. Normalisation      <- normalize.py           cleanText(), unit aliases
     1. Synonym aliases    <- matching layer          SYNONYM_PAIRS, applyAliases()
     2. Jaccard core       <- tfidf_retriever.py      charNgrams / wordNgrams / jaccard
     3. Retriever          <- TfidfRetriever           retrieve() -> Candidate[]
     4. Deterministic      <- DeterministicFilter      deterministicFilter()
     5. Pipeline           <- factory.py               matchItem() orchestrates 3->4
     6. Comparison diff    (tool 1)  diffLists() uses matchItem()
     7. Completeness       (tool 2)  15-category keyword classifier
     8. File import        (tool 3)  drag-drop + dual file inputs + tab switching

   No server calls, no model, no API key — fully deterministic.
   Loaded as <script src="compare-tools.js">; relies on `showToast` and
   the SheetJS (XLSX) global from the main HTML.
   ===================================================================== */


/* -------------------------------------------------------------------------
   0. NORMALISATION  (<- normalize.py  clean_text / unit aliases)
   ---------------------------------------------------------------------- */

const UNIT_ALIASES = {
  'sht\\.?': 'sht',
  // Russian units
  'шт\\.?': 'шт',
  'штук': 'шт',
  'штуки': 'шт',
  'кг\\.?': 'кг',
  'кило': 'кг',
  'килограмм': 'кг',
  'г\\.?': 'г',
  'грамм': 'г',
  'м\\.?': 'м',
  'метр': 'м',
  'метров': 'м',
  'см\\.?': 'см',
  'сантиметр': 'см',
  'мм\\.?': 'мм',
  'миллиметр': 'мм',
  'л\\.?': 'л',
  'литр': 'л',
  'компл\\.?': 'компл',
  'комплект': 'компл',
  'упак\\.?': 'упак',
  'упаковк': 'упак',
  'рул\\.?': 'рул',
  'рулон': 'рул',
  // English
  '\\bpcs?\\.?\\b': 'pcs',
  '\\bpc\\.?\\b': 'pcs',
  '\\bkg\\.?\\b': 'kg',
  '\\bcm\\.?\\b': 'cm',
  '\\bmm\\.?\\b': 'mm',
  '\\bm\\.?\\b': 'm',
};

/**
 * Ported from normalize.py clean_text().
 * NFKC -> lowercase -> strip non-alnum (keeps Cyrillic) -> collapse whitespace -> unit aliases.
 */
function cleanText(value) {
  if (value == null) return '';
  let t = String(value).trim().normalize('NFKC').toLowerCase();
  t = t.replace(/[^a-z0-9а-яёa-z\s]/gi, ' ').replace(/\s+/g, ' ').trim();
  for (const [pat, rep] of Object.entries(UNIT_ALIASES)) {
    t = t.replace(new RegExp(pat, 'g'), rep);
  }
  return t.replace(/\s+/g, ' ').trim();
}

function normalizeItemName(name) {
  return cleanText(name).replace(/[.,;:]+$/, '').trim();
}

function parseListLine(line) {
  var raw = line.trim();
  // Extract [CODE] prefix written by rowsToListText: "[КОД-123] Item name x5"
  var codeM = raw.match(/^\[([^\]]+)\]\s*/);
  var code = codeM ? codeM[1].trim() : null;
  var rest = codeM ? raw.slice(codeM[0].length) : raw;
  var m = rest.match(/^(.*?)(?:\s*[xх]\s*(\d+(?:[.,]\d+)?))\s*$/i);
  var name = (m ? m[1] : rest).trim();
  var qty  = m && m[2] ? Number(m[2].replace(',', '.')) : null;
  return { raw: raw, name: name, qty: qty, key: normalizeItemName(name), code: code };
}

function parseList(rawText) {
  return rawText.split('\n').map(l => l.trim()).filter(Boolean).map(parseListLine);
}


/* -------------------------------------------------------------------------
   1. SYNONYM ALIASES  (<- matching layer)
   ---------------------------------------------------------------------- */

const SYNONYM_PAIRS = [
  [['интерактивная доска','доска интерактивная','умная доска','смарт доска','смарт-доска','интерактивные доски','доски интерактивные'],'интердоска'],
  [['документ-камера','документ камера','визуализатор','докум камера','документ-камеры'],'докками'],
  [['школьная парта','парта школьная','парты школьные','школьные парты','парта ученическая','ученическая парта','парты ученические','ученический стол','стол ученический','столы ученические'],'парта'],
  [['ученический стул','стул ученический','стулья ученические','ученические стулья','стул для учеников','стулья для учеников','стулья для класса','стул для класса'],'стул учен'],
  [['мультимедийный проектор','проектор мультимедийный','мультимедиа проектор'],'проектор'],
  [['компьютер моноблок','моноблок компьютер','персональный компьютер'],'моноблок'],
  [['акустическая система','акустические системы','колонки активные','активные колонки','акустика активная'],'акустика'],
  [['учительский стол','стол учителя','стол педагога','рабочий стол учителя'],'стол учителя'],
  [['шкаф для одежды','шкаф гардеробный','гардеробный шкаф','шкаф для учеников','шкаф ученический','шкафы для одежды'],'шкаф гардероб'],
  [['интерактивная панель','панель интерактивная','тач-панель','сенсорная панель'],'интерпанель'],
];

function applyAliases(text) {
  let t = text;
  for (const [variants, canonical] of SYNONYM_PAIRS) {
    for (const v of variants) {
      if (t.includes(v)) { t = t.split(v).join(canonical); break; }
    }
  }
  return t;
}

function fuzzyKey(name) {
  return applyAliases(normalizeItemName(name)).split(' ').filter(Boolean).sort().join(' ');
}


/* -------------------------------------------------------------------------
   2. JACCARD CORE  (<- tfidf_retriever.py)
   ---------------------------------------------------------------------- */

function charNgrams(str, n) {
  const s = '#'.repeat(n - 1) + str + '#'.repeat(n - 1);
  const ng = new Set();
  for (let i = 0; i <= s.length - n; i++) ng.add(s.slice(i, i + n));
  return ng;
}

function wordNgrams(tokens) {
  const ng = new Set(tokens);
  for (let i = 0; i < tokens.length - 1; i++) ng.add(tokens[i] + ' ' + tokens[i + 1]);
  return ng;
}

function jaccard(a, b) {
  if (!a.size && !b.size) return 0;
  let inter = 0;
  a.forEach(v => { if (b.has(v)) inter++; });
  return inter / (a.size + b.size - inter);
}

const _RU_SUFFIXES = [
  'ского','ской','ских','ским','ские','ская','ский',
  'ного','ной','ных','ным','ними','ние','ные','ная','ный',
  'ого','ому','его','ему',
  'ыми','ыме','ые','ый','ых','ым',
  'ими','ие','ии','ий','их','им',
  'ью','ая','ое','ям','ах',
];

function stemRu(w) {
  if (w.length < 5) return w;
  for (const sfx of _RU_SUFFIXES) {
    if (w.endsWith(sfx) && w.length - sfx.length >= 3) return w.slice(0, -sfx.length);
  }
  return w;
}

function nameSimilarity(a, b) {
  const ka = cleanText(a), kb = cleanText(b);
  if (!ka || !kb) return 0;
  if (ka === kb) return 1;
  const aa = applyAliases(ka), ab = applyAliases(kb);
  const tokA = aa.split(' ').filter(Boolean).map(stemRu).sort();
  const tokB = ab.split(' ').filter(Boolean).map(stemRu).sort();
  const wordScore = jaccard(wordNgrams(tokA), wordNgrams(tokB));
  const charScore = jaccard(charNgrams(ka, 3), charNgrams(kb, 3));
  return wordScore * 0.55 + charScore * 0.45;
}


/* -------------------------------------------------------------------------
   3. RETRIEVER  (<- TfidfRetriever.get_top_k())
   ---------------------------------------------------------------------- */

function makeCandidate(item, score, explanation, codeMatched = false) {
  return { name: item.name, qty: item.qty, key: item.key, code: item.code || null,
           score, explanation, codeMatched };
}

function retrieve(queryItem, catalog, k = 5) {
  return catalog
    .map(catItem => {
      const score = nameSimilarity(queryItem.name, catItem.name);
      const ka = applyAliases(cleanText(queryItem.name));
      const kb = applyAliases(cleanText(catItem.name));
      const tA = ka.split(' ').filter(Boolean).map(stemRu).sort();
      const tB = kb.split(' ').filter(Boolean).map(stemRu).sort();
      const wordS = jaccard(wordNgrams(tA), wordNgrams(tB));
      const charS = jaccard(charNgrams(cleanText(queryItem.name), 3), charNgrams(cleanText(catItem.name), 3));
      const expl = 'hybrid Jaccard ' + Math.round(score * 100) + '% (' +
        'слово ' + Math.round(wordS * 100) + '%, ' +
        'символ ' + Math.round(charS * 100) + '%)';
      return makeCandidate(catItem, score, expl, false);
    })
    .filter(c => c.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, k);
}


/* -------------------------------------------------------------------------
   4. DETERMINISTIC FILTER  (<- DeterministicFilter.filter())
   ---------------------------------------------------------------------- */

const FUZZY_MATCH_THRESHOLD = 0.32;

function codeRatio(a, b) {
  if (a === b) return 1;
  if (!a || !b) return 0;
  return jaccard(charNgrams(a, 2), charNgrams(b, 2));
}

function deterministicFilter(queryItem, candidates, minScore) {
  if (minScore === undefined) minScore = FUZZY_MATCH_THRESHOLD;
  const qCode = ((queryItem.code || '')).trim().toUpperCase();
  return candidates
    .map(c => {
      let { score, explanation, codeMatched } = c;
      const cCode = ((c.code || '')).trim().toUpperCase();
      if (qCode && cCode) {
        if (qCode === cCode) {
          score = Math.max(score, 0.95);
          explanation += '; точное совпадение кода';
          codeMatched = true;
        } else {
          const ratio = codeRatio(qCode, cCode);
          if (ratio > 0.8) {
            score = Math.max(score, Math.min(0.9, score + 0.2));
            explanation += '; похожий код (' + Math.round(ratio * 100) + '%)';
            codeMatched = true;
          }
        }
      }
      return Object.assign({}, c, { score, explanation, codeMatched });
    })
    .filter(c => c.score >= minScore || c.codeMatched);
}


/* -------------------------------------------------------------------------
   5. PIPELINE  (<- factory.py)
   ---------------------------------------------------------------------- */

/* Classification cache — each unique name is classified once; hits on repeated
   catalog items are O(1). Resets when new files are loaded or matching runs. */
var _catCache = {};
function classifyItemC(name) {
  return _catCache[name] || (_catCache[name] = classifyItem(name));
}

function matchItem(queryItem, catalog) {
  // Stage 1: category pre-filter — narrow the catalog to the same category as the query.
  // Falls back to full catalog if query is 'Прочее' or no same-category items exist.
  var qCats = classifyItemC(queryItem.name);
  var pool = catalog;
  if (qCats[0] !== 'Прочее') {
    var catFiltered = catalog.filter(function(catItem) {
      var cCats = classifyItemC(catItem.name);   // cached — O(1) on repeat
      return cCats.some(function(c) { return qCats.indexOf(c) !== -1; });
    });
    if (catFiltered.length > 0) pool = catFiltered;
    // if no same-category items found, pool stays = full catalog (safe fallback)
  }

  // Stage 2: Jaccard top-K within the category pool
  var candidates = retrieve(queryItem, pool);

  // Stage 3: code-inject — always search the FULL catalog for code matches,
  // not just the pool, so a code hit always wins regardless of category.
  var qCode = ((queryItem.code || '')).trim().toUpperCase();
  if (qCode) {
    var inPool = new Set(candidates.map(function(c) { return c.key; }));
    catalog.forEach(function(catItem) {
      if (inPool.has(catItem.key)) return;
      var cCode = ((catItem.code || '')).trim().toUpperCase();
      if (!cCode) return;
      if (qCode === cCode || codeRatio(qCode, cCode) > 0.8) {
        candidates.push(makeCandidate(catItem, 0, 'код совпадение', false));
      }
    });
  }

  // Stage 4: deterministic filter + best
  var final = deterministicFilter(queryItem, candidates);
  if (!final.length) return null;
  return final.sort(function(a, b) { return b.score - a.score; })[0];
}


/* -------------------------------------------------------------------------
   6. COMPARISON / DIFF  (tool 1)
   ---------------------------------------------------------------------- */

function diffLists(itemsA, itemsB) {
  const mapA = new Map(itemsA.map(it => [it.key, it]));
  const mapB = new Map(itemsB.map(it => [it.key, it]));
  const onlyA = [], onlyB = [], mismatched = [], matched = [];

  mapA.forEach((itA, key) => {
    if (!mapB.has(key)) { onlyA.push(itA); return; }
    const itB = mapB.get(key);
    if ((itA.qty ?? null) !== (itB.qty ?? null)) {
      mismatched.push({ name: itA.name, qtyA: itA.qty, qtyB: itB.qty });
    } else {
      matched.push(itA);
    }
  });
  mapB.forEach((itB, key) => { if (!mapA.has(key)) onlyB.push(itB); });

  const likely = [], usedBKeys = new Set(), stillOnlyA = [];
  onlyA.forEach(itA => {
    const remaining = onlyB.filter(itB => !usedBKeys.has(itB.key));
    const best = matchItem(itA, remaining);
    if (best) {
      likely.push({ nameA: itA.name, nameB: best.name, qtyA: itA.qty, qtyB: best.qty,
                    score: best.score, explanation: best.explanation, codeMatched: best.codeMatched });
      usedBKeys.add(best.key);
    } else {
      stillOnlyA.push(itA);
    }
  });

  return { onlyA: stillOnlyA, onlyB: onlyB.filter(itB => !usedBKeys.has(itB.key)),
           mismatched, matched, likely };
}

function renderCompareResults(diff) {
  const wrap = document.getElementById('compareResultsWrap');
  const total = diff.matched.length + diff.mismatched.length + diff.likely.length + diff.onlyA.length;

  function confStyle(score, codeMatched) {
    if (codeMatched)   return { bg: '#ede9fe', fg: '#6d28d9', label: 'код ✓' };
    if (score >= 0.60) return { bg: '#dcfce7', fg: '#15803d', label: Math.round(score * 100) + '%' };
    if (score >= 0.32) return { bg: '#fef3c7', fg: '#b45309', label: Math.round(score * 100) + '%' };
    return             { bg: '#f1f5f9', fg: '#94a3b8', label: '—' };
  }

  const pill = (bg, fg, icon, n, label) => n
    ? '<span style="display:inline-flex;align-items:center;gap:5px;padding:5px 13px;border-radius:20px;background:' + bg + ';color:' + fg + ';font-size:12.5px;font-weight:600;">' + icon + ' ' + n + ' ' + label + '</span>'
    : '';

  let html = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px;align-items:center;">'
    + '<span style="font-size:12px;color:var(--text-dim);margin-right:4px;">Итого ' + total + ' позиций:</span>'
    + pill('#dcfce7', '#15803d', '✓', diff.matched.length,   'совпадают')
    + pill('#ede9fe', '#6d28d9', '≈', diff.likely.length,    'аналогов')
    + pill('#fef3c7', '#b45309', '!',      diff.mismatched.length,'расх. по кол-ву')
    + pill('#fee2e2', '#dc2626', '✗', diff.onlyA.length,     'только в А')
    + pill('#f0fdf4', '#15803d', '+',      diff.onlyB.length,     'только в Б')
    + '</div>';

  const tableRows = [];
  diff.likely.forEach(it => {
    tableRows.push({ nameA: it.nameA, qtyA: it.qtyA, nameB: it.nameB, qtyB: it.qtyB,
                     s: confStyle(it.score, it.codeMatched), tip: it.explanation, rowBg: '' });
  });
  diff.mismatched.forEach(it => {
    tableRows.push({ nameA: it.name, qtyA: it.qtyA, nameB: it.name, qtyB: it.qtyB,
                     s: { bg: '#fef3c7', fg: '#b45309', label: 'кол-во ≠' },
                     tip: 'Разное количество', rowBg: '#fffbeb' });
  });
  diff.onlyA.forEach(it => {
    tableRows.push({ nameA: it.name, qtyA: it.qty, nameB: '—', qtyB: null,
                     s: { bg: '#fee2e2', fg: '#dc2626', label: 'нет в Б' },
                     tip: 'Отсутствует в комплектации Б', rowBg: '#fff5f5' });
  });

  if (tableRows.length) {
    html += '<div style="border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:20px;">'
      + '<table style="width:100%;border-collapse:collapse;font-size:12.5px;">'
      + '<thead><tr style="background:var(--navy);">'
      + '<th style="text-align:left;padding:10px 14px;color:#fff;font-weight:600;width:38%;">Комплектация А</th>'
      + '<th style="text-align:left;padding:10px 14px;color:#fff;font-weight:600;width:38%;">Комплектация Б / совпадение</th>'
      + '<th style="text-align:center;padding:10px 14px;color:#fff;font-weight:600;width:12%;">Статус</th>'
      + '<th style="text-align:right;padding:10px 14px;color:#fff;font-weight:600;width:12%;">Кол-во А→Б</th>'
      + '</tr></thead><tbody>'
      + tableRows.map(function(r, i) {
          const qtyCell = r.qtyB !== null && r.qtyB !== r.qtyA
            ? (r.qtyA !== null ? r.qtyA : '—') + ' → <b style="color:#b45309;">' + r.qtyB + '</b>'
            : (r.qtyA !== null ? r.qtyA : '—');
          return '<tr style="background:' + (r.rowBg || (i % 2 === 0 ? '#fff' : '#f8fafc')) + ';" title="' + r.tip + '">'
            + '<td style="padding:9px 14px;border-bottom:1px solid var(--border);">' + r.nameA + '</td>'
            + '<td style="padding:9px 14px;border-bottom:1px solid var(--border);color:' + (r.nameB === '—' ? 'var(--text-dim)' : 'var(--text)') + ';">' + r.nameB + '</td>'
            + '<td style="padding:9px 14px;border-bottom:1px solid var(--border);text-align:center;">'
            +   '<span style="background:' + r.s.bg + ';color:' + r.s.fg + ';border-radius:8px;padding:2px 9px;font-size:11.5px;font-weight:700;white-space:nowrap;">' + r.s.label + '</span>'
            + '</td>'
            + '<td style="padding:9px 14px;border-bottom:1px solid var(--border);text-align:right;color:var(--text-dim);">' + qtyCell + '</td>'
            + '</tr>';
        }).join('')
      + '</tbody></table></div>';
  }

  if (diff.matched.length) {
    html += '<details style="margin-bottom:16px;">'
      + '<summary style="cursor:pointer;font-size:12.5px;color:var(--text-dim);padding:8px 14px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;list-style:none;display:flex;align-items:center;gap:8px;">'
      + '<span style="color:#15803d;font-weight:700;">✓ ' + diff.matched.length + ' позиций совпадают полностью</span>'
      + '<span style="margin-left:auto;font-size:11px;">развернуть ▸</span>'
      + '</summary>'
      + '<div style="border:1px solid var(--border);border-top:none;border-radius:0 0 8px 8px;overflow:hidden;">'
      + diff.matched.map(function(it, i) {
          return '<div style="padding:7px 14px;font-size:12.5px;background:' + (i % 2 === 0 ? '#fff' : '#f8fafc') + ';border-bottom:1px solid var(--border);">'
            + it.name + (it.qty !== null ? ' <span style="color:var(--text-dim);">x' + it.qty + '</span>' : '')
            + '</div>';
        }).join('')
      + '</div></details>';
  }

  if (diff.onlyB.length) {
    html += '<div class="result-block">'
      + '<div class="result-head"><b>Только в комплектации Б</b><span class="badge badge-green">' + diff.onlyB.length + '</span></div>'
      + '<div style="padding:10px 14px;">'
      + diff.onlyB.map(function(it, i) {
          return '<div style="padding:6px 0;font-size:12.5px;' + (i < diff.onlyB.length - 1 ? 'border-bottom:1px solid var(--border)' : '') + '">'
            + it.name + (it.qty !== null ? ' <span style="color:var(--text-dim);">x' + it.qty + '</span>' : '')
            + '</div>';
        }).join('')
      + '</div></div>';
  }

  if (!tableRows.length && !diff.matched.length && !diff.onlyB.length) {
    html = '<div class="card" style="border-color:var(--green);background:var(--green-bg);color:var(--green);font-weight:600;font-size:13px;">Расхождений не найдено.</div>';
  }

  wrap.innerHTML = html;
}

document.getElementById('runCompareBtn').addEventListener('click', function() {
  _catCache = {};   // clear cache between runs
  var rawA = document.getElementById('compareBoxA').value.trim();
  var rawB = document.getElementById('compareBoxB').value.trim();
  if (!rawA || !rawB) { showToast('Вставьте обе комплектации (А и Б) перед сравнением.'); return; }
  renderCompareResults(diffLists(parseList(rawA), parseList(rawB)));
});


/* -------------------------------------------------------------------------
   6b. CATALOG MATCHING  (tool 3)
   Mirrors product_matching_engine pipeline: category pre-filter → Jaccard
   top-K → code-inject → deterministic filter → HeuristicRanker field scores.
   Returns top-3 варианты per item (like Python TOP_N_RESULTS = 3).
   ---------------------------------------------------------------------- */

var _cmpQueryItems  = [];   // structured items from "Ваши позиции" file
var _cmpCatalogItems = [];  // structured items from "Каталог" file
var _cmpSelections   = {};  // { itemIndex: matchVariantIndex } — user radio picks
var _cmpResults      = [];  // last renderCatalogMatch() output (for export)

/* Field-aware score — mirrors Python HeuristicRanker weights.
   weights: name 35%, jaccard 30%, brand 20%, model 15% */
function fieldAwareScore(queryItem, candidate) {
  var nameScore  = nameSimilarity(queryItem.name, candidate.name);
  var jaccScore  = candidate.score;
  var brandScore = 0, modelScore = 0;
  if (queryItem.brand && candidate.brand) {
    brandScore = cleanText(queryItem.brand) === cleanText(candidate.brand) ? 1.0 : 0.3;
  }
  if (queryItem.model && candidate.model) {
    var qm = cleanText(queryItem.model), cm = cleanText(candidate.model);
    modelScore = qm === cm ? 1.0 : (qm.includes(cm) || cm.includes(qm)) ? 0.5 : 0.0;
  }
  return Math.min(1.0, 0.35 * nameScore + 0.30 * jaccScore + 0.20 * brandScore + 0.15 * modelScore);
}

/* matchItemTopK: returns up to k best candidates for one query item.
   Uses cached classifyItemC so repeated catalog items are O(1). */
function matchItemTopK(queryItem, catalog, k) {
  k = k || 3;
  var qCats = classifyItemC(queryItem.name);
  var pool = catalog;
  if (qCats[0] !== 'Прочее') {
    var flt = catalog.filter(function(c) {
      return classifyItemC(c.name).some(function(x) { return qCats.indexOf(x) !== -1; });
    });
    if (flt.length > 0) pool = flt;
  }
  // Retrieve wider candidate set than Section 3 default (top-25 or 5×k)
  var candidates = retrieve(queryItem, pool, Math.max(k * 5, 25));

  // Code-inject from full catalog (not just pool)
  var qCode = (queryItem.code || '').trim().toUpperCase();
  if (qCode) {
    var inSet = new Set(candidates.map(function(c) { return c.key; }));
    catalog.forEach(function(catItem) {
      if (inSet.has(catItem.key)) return;
      var cCode = (catItem.code || '').trim().toUpperCase();
      if (!cCode) return;
      if (qCode === cCode || codeRatio(qCode, cCode) > 0.8) {
        candidates.push(makeCandidate(catItem, 0, 'код совпадение', false));
      }
    });
  }

  // Deterministic filter (applies code boosts, removes below threshold)
  var filtered = deterministicFilter(queryItem, candidates);

  // Apply field-aware re-scoring — take max of Jaccard and blended score
  filtered = filtered.map(function(c) {
    var blended = fieldAwareScore(queryItem, c);
    return Object.assign({}, c, { score: Math.max(c.score, blended) });
  });

  return filtered.sort(function(a, b) { return b.score - a.score; }).slice(0, k);
}

/* Load an Excel/CSV into structured item objects (code/brand/model preserved). */
function loadStructuredFile(file, callback) {
  if (typeof XLSX === 'undefined') { showToast('Библиотека XLSX не загружена.'); return; }
  var ext = file.name.split('.').pop().toLowerCase();
  var reader = new FileReader();
  reader.onload = function(ev) {
    try {
      var wb = ext === 'csv'
        ? XLSX.read(ev.target.result, { type: 'binary' })
        : XLSX.read(new Uint8Array(ev.target.result), { type: 'array' });
      var ws   = wb.Sheets[wb.SheetNames[0]];
      var sRows = XLSX.utils.sheet_to_json(ws, { header: 1, raw: true, defval: '' });
      var res   = rowsFromSheet(sRows);
      if (!res.rows.length) { showToast('Не удалось распознать строки в файле.'); return; }
      var items = res.rows.map(function(r) {
        return { name: r.name, qty: r.qty, key: normalizeItemName(r.name),
                 code: r.code, brand: r.brand || null, model: r.model || null, specs: r.specs || null };
      });
      callback(items, file.name);
    } catch(err) { showToast('Ошибка чтения файла: ' + err.message); }
  };
  reader.onerror = function() { showToast('Не удалось прочитать файл.'); };
  if (ext === 'csv') reader.readAsBinaryString(file);
  else reader.readAsArrayBuffer(file);
}

function updateCatalogZoneLabel(elId, text) {
  var el = document.getElementById(elId);
  if (el) el.innerHTML = '<span style="font-size:12.5px;">' + text + '</span>';
}

function renderCatalogMatch() {
  var wrap = document.getElementById('catalogMatchResultsWrap');
  if (!wrap) return;
  if (!_cmpQueryItems.length || !_cmpCatalogItems.length) {
    wrap.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-dim);font-size:13px;">Загрузите оба файла перед подбором.</div>';
    return;
  }

  _catCache = {};       // fresh cache for this run
  _cmpSelections = {};

  var results = _cmpQueryItems.map(function(item) {
    return { item: item, matches: matchItemTopK(item, _cmpCatalogItems, 3) };
  });
  _cmpResults = results;

  var matchedCount = results.filter(function(r) { return r.matches.length; }).length;

  var html = '<div style="margin-bottom:16px;display:flex;align-items:center;gap:12px;">'
    + '<span style="font-size:13px;font-weight:600;">Совпадения: ' + matchedCount + ' из ' + results.length + ' позиций</span>'
    + '<button id="catalogExportBtn" class="btn btn-primary" style="margin-left:auto;font-size:12px;padding:6px 18px;">⬇ Excel</button>'
    + '</div>';

  results.forEach(function(r, idx) {
    html += '<div style="border:1px solid var(--border);border-radius:12px;margin-bottom:12px;overflow:hidden;">';

    // ── Item header ──
    html += '<div style="background:var(--bg);padding:9px 14px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;">'
      + '<span style="font-size:11px;font-weight:700;color:var(--text-dim);min-width:24px;">#' + (idx + 1) + '</span>'
      + '<span style="font-size:13px;font-weight:600;flex:1;">' + r.item.name + '</span>'
      + (r.item.code  ? '<span style="font-size:10.5px;background:#f1f5f9;color:var(--text-dim);border-radius:4px;padding:1px 7px;white-space:nowrap;">' + r.item.code + '</span>' : '')
      + (r.item.brand ? '<span style="font-size:10.5px;color:var(--text-dim);">' + r.item.brand + '</span>' : '')
      + (r.item.qty   ? '<span style="margin-left:4px;font-size:12px;color:var(--text-dim);">x' + r.item.qty + '</span>' : '')
      + '</div>';

    if (!r.matches.length) {
      html += '<div style="padding:11px 14px;font-size:12.5px;color:var(--text-dim);">⊘ Совпадений в каталоге не найдено</div>';
    } else {
      html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));">';
      r.matches.forEach(function(m, mi) {
        var conf   = m.codeMatched ? 'код ✓' : Math.round(m.score * 100) + '%';
        var confBg = m.codeMatched ? '#ede9fe' : m.score >= 0.60 ? '#dcfce7' : m.score >= 0.32 ? '#fef3c7' : '#f1f5f9';
        var confFg = m.codeMatched ? '#6d28d9' : m.score >= 0.60 ? '#15803d' : m.score >= 0.32 ? '#b45309' : '#94a3b8';
        var rid = 'cmpR_' + idx + '_' + mi;
        html += '<label for="' + rid + '" '
          + 'style="display:block;padding:11px 13px;border-right:1px solid var(--border);cursor:pointer;" '
          + 'onmouseover="this.style.background=\'#f8fafc\'" onmouseout="this.style.background=\'\'">'
          + '<div style="display:flex;gap:8px;">'
          + '<input type="radio" id="' + rid + '" name="cmpSel_' + idx + '" value="' + mi + '" style="margin-top:3px;flex-shrink:0;" '
          + 'onchange="_cmpSelections[' + idx + ']=' + mi + ';" ' + (mi === 0 ? 'checked' : '') + '>'
          + '<div style="flex:1;min-width:0;">'
          + '<div style="font-size:10.5px;font-weight:700;color:var(--text-dim);margin-bottom:3px;">ВАРИАНТ ' + (mi + 1) + '</div>'
          + '<div style="font-size:14px;font-weight:800;color:' + confFg + ';margin-bottom:5px;">' + conf + '</div>'
          + (m.code ? '<div style="font-size:10px;color:var(--text-dim);margin-bottom:4px;font-family:monospace;">' + m.code + '</div>' : '')
          + '<div style="font-size:12px;color:var(--text);line-height:1.45;">' + m.name + '</div>'
          + (m.explanation ? '<div style="font-size:10px;color:var(--text-dim);margin-top:4px;font-style:italic;line-height:1.3;">' + m.explanation + '</div>' : '')
          + '</div></div></label>';
      });
      // Pre-select first variant
      if (_cmpSelections[idx] === undefined) _cmpSelections[idx] = 0;
      html += '</div>';
    }
    html += '</div>';
  });

  wrap.innerHTML = html;

  var exportBtn = document.getElementById('catalogExportBtn');
  if (exportBtn) exportBtn.addEventListener('click', exportCatalogMatch);
}

function exportCatalogMatch() {
  if (typeof XLSX === 'undefined') { showToast('XLSX не загружена'); return; }
  var rows = [['#','Позиция','Код','Кол-во','Подобранный вариант','Код каталога','Уверенность','Пояснение']];
  _cmpResults.forEach(function(r, idx) {
    var sel = _cmpSelections[idx] !== undefined ? _cmpSelections[idx] : 0;
    var m   = r.matches[sel];
    if (m) {
      rows.push([idx + 1, r.item.name, r.item.code || '', r.item.qty || '',
                 m.name, m.code || '',
                 m.codeMatched ? 'Код ✓' : Math.round(m.score * 100) + '%',
                 m.explanation || '']);
    } else {
      rows.push([idx + 1, r.item.name, r.item.code || '', r.item.qty || '', '— не найдено', '', '', '']);
    }
  });
  var wb = XLSX.utils.book_new();
  var ws = XLSX.utils.aoa_to_sheet(rows);
  ws['!cols'] = [{wch:4},{wch:42},{wch:14},{wch:8},{wch:42},{wch:14},{wch:11},{wch:38}];
  XLSX.utils.book_append_sheet(wb, ws, 'Подбор');
  XLSX.writeFile(wb, 'catalog_match.xlsx');
}


/* -------------------------------------------------------------------------
   7. COMPLETENESS CHECKER  (tool 2)
   ---------------------------------------------------------------------- */

const CATEGORY_KEYWORDS = {
  // --- universal ---
  'Мебель':                     ['парт','стул','стол','шкаф','стеллаж','кресл','диван','тумб','скамь','вешалк','полк','стенд',
                                  'кроват','двухъярусн','манеж','пеленальн','рундук'],
  'Техника':                    ['доск','камер','проектор','экран','ноутбук','компьютер','моноблок','монитор','принтер','колонк','акустик','микрофон','усилител','пульт','зарядн','роутер','сервер','планшет','wifi','телевизор','blu-ray','ресивер'],
  'Музыкальные инструменты':    ['рояль','пианино','фортепиан','скрипк','виолончел','альт','контрабас','флейт','гобой','кларнет','фаготт','валторн','труб','тромбон','туб','балалайк','домр','гитар','баян','аккордеон','орган','синтезатор','барабан','ксилофон','маримб','ударн'],
  'Осветительное оборудование': ['прожектор','светильник','люстр','лампа','диммер','led','светодиод','трек','рампа','подсветк'],
  'Сценическое оборудование':   ['занавес','кулис','задник','штанкет','микшер','микшерн','монитор сцен','стойк'],
  'Климат и вентиляция':        ['кондиционер','сплит','вентилятор','тепловентилятор','обогреватель','рекуператор','вентиляц','увлажнитель'],
  'Безопасность':               ['видеонаблюд','видеокамера','огнетушитель','пожарн','турникет','замок','электрозамок','кнопка вызов','сигнализац','видеодомофон'],
  'Дидактика':                  ['учебник','плакат','наглядн','методич','нотн','ноты','пособие','карточк','раздаточ','дидактич','таблиц','атлас','словарь','книга','литератур',
                                  'пластилин','краск','альбом','раскраск','карандаш','фломастер'],
  'Спортивное оборудование':    ['спортивн','брусья','шведск','матер','мяч','кольц','ворот','тренажер','гантел','штанг','гимнастическ','секундомер',
                                  'турник','канат','обруч','скакалк',
                                  'бревно','кегл','велосипед','санки','лыж','клюшк','скамейк гимнастическ','бадминтон','баскетбол','волейбол','футбол'],
  'Лабораторное оборудование':  ['микроскоп','лупа','пробирк','реактив','лаборатор','штатив','весы','мензурк','химическ'],
  'Библиотека':                 ['стеллаж книг','библиотечн','картотек','читальн','выставочн'],
  'Уборочный инвентарь':        ['швабр','ведр','тряпк','щётк','совок','уборочн','пылесос','мойк','стирал','сушильн','утюг','гладильн'],
  'Хозяйственный инвентарь':    ['хозяйственн','стремянк','лестниц','тележк','отвёртк','молоток','дрел','шуруповёрт'],
  'Посуда и кухня':             ['посуд','тарелк','кружк','стакан','чайник','кофемашин','микроволн','холодильник','водонагреватель','диспенсер','кулер',
                                  'котел','термос'],
  // --- kindergarten ---
  'Игровое оборудование':       ['горк','качел','карусел','песочниц','батут','конструктор','пазл','игрушк','кукл','игровой','балансир','кубик','сортер','пирамидк','развивающ',
                                  'беседк','качалк','лабиринт','мозаик','мольберт'],
  'Постельные принадлежности':  ['матрас','подушк','одеял','простын','пододеяльник','наволочк','спальн','покрывал','лежак',
                                  'наматрасник','полотенц','ковер'],
  // --- military camp ---
  'Полевое оборудование':       ['палатк','рация','маскировочн','полос препятствий','генератор'],
  'Медицинское оборудование':   ['носилк','кушетк','аптечк','перевязочн','медицинск','бинт','шприц','тонометр','медпункт'],
  // --- laundry (kindergarten, camp) ---
  'Прачечная и бельё':          ['стирал','сушильн','утюг','гладильн','тележк бель','стеллаж бель'],
  // --- fallback ---
  'Прочее':                     [],
};

const REQUIRED_CATEGORIES = ['Мебель', 'Техника', 'Дидактика'];

function classifyItem(name) {
  var n = cleanText(name);
  var matches = Object.keys(CATEGORY_KEYWORDS).filter(function(cat) {
    if (cat === 'Прочее') return false;
    return CATEGORY_KEYWORDS[cat].some(function(kw) { return n.includes(kw); });
  });
  return matches.length ? matches : ['Прочее'];
}

function checkCompleteness(items) {
  var categoriesFound = new Set();
  items.forEach(function(it) { classifyItem(it.name).forEach(function(c) { categoriesFound.add(c); }); });
  var missingCategories = REQUIRED_CATEGORIES.filter(function(c) { return !categoriesFound.has(c); });
  var noQty = items.filter(function(it) { return it.qty === null; });
  var seen = new Map();
  items.forEach(function(it) { seen.set(it.key, (seen.get(it.key) || 0) + 1); });
  var duplicates = Array.from(seen.entries())
    .filter(function(e) { return e[1] > 1; })
    .map(function(e) { return { name: items.find(function(it) { return it.key === e[0]; }).name, count: e[1] }; });
  var breakdown = {};
  items.forEach(function(it) {
    classifyItem(it.name).forEach(function(c) { breakdown[c] = (breakdown[c] || 0) + 1; });
  });
  return { missingCategories: missingCategories, noQty: noQty, duplicates: duplicates, breakdown: breakdown };
}

function renderCompletenessResults(result, totalItems) {
  var wrap = document.getElementById('completenessResultsWrap');
  var html = '<div class="card-title" style="margin-bottom:10px;">'
    + 'Результаты проверки '
    + '<span class="demo-flag" style="background:#f2f4f7;color:var(--text-dim);">' + (Object.keys(CATEGORY_KEYWORDS).length - 1) + ' категорий · ключевые слова</span></div>';

  var sorted = Object.entries(result.breakdown).sort(function(a, b) { return b[1] - a[1]; });
  if (sorted.length) {
    html += '<div class="result-block" style="margin-bottom:14px;">'
      + '<div class="result-head"><b>Распределение по категориям</b><span class="badge" style="background:#f1f5f9;color:var(--text-dim);">' + sorted.length + '</span></div>'
      + '<div style="padding:10px 16px;">'
      + sorted.map(function(e) {
          return '<span style="display:inline-flex;align-items:center;gap:4px;background:var(--bg);border:1px solid var(--border);border-radius:20px;padding:3px 10px;font-size:11.5px;margin:3px 3px 3px 0;">'
            + '<b style="color:var(--navy);">' + e[0] + '</b>'
            + '<span style="color:var(--text-dim);">' + e[1] + ' поз.</span>'
            + '</span>';
        }).join('')
      + '</div></div>';
  }

  if (result.missingCategories.length) {
    html += '<div class="result-block"><div class="result-head"><b>Обязательные категории без совпадений</b><span class="badge badge-amber">' + result.missingCategories.length + '</span></div>'
      + '<div style="padding:12px 16px;font-size:13px;color:var(--text-dim);">Проверьте, что это не пропуск: ' + result.missingCategories.join(', ') + '.</div></div>';
  }
  if (result.noQty.length) {
    html += '<div class="result-block"><div class="result-head"><b>Позиции без распознанного количества</b><span class="badge badge-gray">' + result.noQty.length + '</span></div>'
      + '<div style="padding:12px 16px;">'
      + result.noQty.map(function(it) { return '<div class="item-row"><span class="item-name">' + it.name + '</span><span class="qty">добавьте "x&lt;число&gt;"</span></div>'; }).join('')
      + '</div></div>';
  }
  if (result.duplicates.length) {
    html += '<div class="result-block"><div class="result-head"><b>Повторяющиеся наименования</b><span class="badge badge-red">' + result.duplicates.length + '</span></div>'
      + '<div style="padding:12px 16px;">'
      + result.duplicates.map(function(d) { return '<div class="item-row"><span class="item-name">' + d.name + '</span><span class="qty">встречается ' + d.count + ' раза</span></div>'; }).join('')
      + '</div></div>';
  }
  if (!result.missingCategories.length && !result.noQty.length && !result.duplicates.length) {
    html += '<div class="card" style="border-color:var(--green);background:var(--green-bg);color:var(--green);font-weight:600;font-size:13px;">Замечаний не найдено по ' + totalItems + ' позициям.</div>';
  }
  wrap.innerHTML = html;
}

document.getElementById('runCompletenessBtn').addEventListener('click', function() {
  var raw = document.getElementById('completenessBox').value.trim();
  if (!raw) { showToast('Вставьте перечень позиций перед проверкой.'); return; }
  var items = parseList(raw);
  renderCompletenessResults(checkCompleteness(items), items.length);
});


/* -------------------------------------------------------------------------
   8. FILE IMPORT + DRAG-DROP + TAB SWITCHING
   ---------------------------------------------------------------------- */

const PRICE_LIST_HEADER_HINTS = {
  name:  ['наименован','назван','товар','позици','номенклатур','item','name','наим'],
  qty:   ['кол-во','количество','шт','qty','quantity','штук','объем','кол во'],
  price: ['цена','стоимост','price','сумма','тариф','cost','rate'],
  code:  ['код','артикул','article','sku','code','номер'],
  brand: ['бренд','марка','производитель','brand','manufacturer'],
  model: ['модель','model','серия'],
  specs: ['характеристик','спецификац','specs','техн описан','description'],
};

function guessColumnIndexes(headerRow) {
  var lower = headerRow.map(function(h) { return String(h || '').toLowerCase().trim(); });
  function findCol(hints) {
    for (var i = 0; i < lower.length; i++) {
      if (hints.some(function(h) { return lower[i].includes(h); })) return i;
    }
    return -1;
  }
  return { name:  findCol(PRICE_LIST_HEADER_HINTS.name),
           qty:   findCol(PRICE_LIST_HEADER_HINTS.qty),
           price: findCol(PRICE_LIST_HEADER_HINTS.price),
           code:  findCol(PRICE_LIST_HEADER_HINTS.code),
           brand: findCol(PRICE_LIST_HEADER_HINTS.brand),
           model: findCol(PRICE_LIST_HEADER_HINTS.model),
           specs: findCol(PRICE_LIST_HEADER_HINTS.specs) };
}

function parseMoneyValue(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  if (typeof raw === 'number') return raw;
  var cleaned = String(raw).replace(/[^\d.,-]/g, '').replace(',', '.');
  return cleaned && !isNaN(Number(cleaned)) ? Number(cleaned) : null;
}

function rowsFromSheet(sheetRows) {
  var nonEmpty = sheetRows.filter(function(r) { return r.some(function(c) { return String(c || '').trim() !== ''; }); });
  if (!nonEmpty.length) return { rows: [], cols: null };
  var cols = guessColumnIndexes(nonEmpty[0]);
  var dataStart = 1;
  if (cols.name === -1) { cols = { name: 0, qty: -1, price: -1, code: -1, brand: -1, model: -1, specs: -1 }; dataStart = 0; }
  var rows = nonEmpty.slice(dataStart).map(function(r) {
    return {
      name:  String(r[cols.name]  || '').trim(),
      qty:   cols.qty   >= 0 ? parseMoneyValue(r[cols.qty])   : null,
      price: cols.price >= 0 ? parseMoneyValue(r[cols.price]) : null,
      code:  cols.code  >= 0 ? String(r[cols.code]  || '').trim() || null : null,
      brand: cols.brand >= 0 ? String(r[cols.brand] || '').trim() || null : null,
      model: cols.model >= 0 ? String(r[cols.model] || '').trim() || null : null,
      specs: cols.specs >= 0 ? String(r[cols.specs] || '').trim() || null : null,
    };
  }).filter(function(r) { return r.name; });
  return { rows: rows, cols: cols };
}

function rowsToListText(rows) {
  return rows.map(function(r) {
    var prefix = r.code ? '[' + r.code + '] ' : '';
    return prefix + (r.qty != null ? r.name + ' x' + r.qty : r.name);
  }).join('\n');
}

// -- File -> textarea -------------------------------------------------
function loadFileIntoTextarea(file, textareaId) {
  if (!file) return;
  if (typeof XLSX === 'undefined') { showToast('Библиотека XLSX не загружена. Обновите страницу.'); return; }
  var ext = file.name.split('.').pop().toLowerCase();
  var reader = new FileReader();
  reader.onload = function(ev) {
    try {
      var wb = ext === 'csv'
        ? XLSX.read(ev.target.result, { type: 'binary' })
        : XLSX.read(new Uint8Array(ev.target.result), { type: 'array' });
      var ws = wb.Sheets[wb.SheetNames[0]];
      var sheetRows = XLSX.utils.sheet_to_json(ws, { header: 1, raw: true, defval: '' });
      var result = rowsFromSheet(sheetRows);
      if (!result.rows.length) { showToast('Не удалось распознать строки в файле.'); return; }
      document.getElementById(textareaId).value = rowsToListText(result.rows);
      showToast('Загружено ' + result.rows.length + ' позиций из «' + file.name + '»');
    } catch (err) { showToast('Ошибка чтения файла: ' + err.message); }
  };
  reader.onerror = function() { showToast('Не удалось прочитать файл.'); };
  if (ext === 'csv') reader.readAsBinaryString(file);
  else reader.readAsArrayBuffer(file);
}

// -- File input listeners ---------------------------------------------
document.getElementById('priceListFileA').addEventListener('change', function(e) {
  loadFileIntoTextarea(e.target.files[0], 'compareBoxA');
});
document.getElementById('priceListFileB').addEventListener('change', function(e) {
  loadFileIntoTextarea(e.target.files[0], 'compareBoxB');
});
document.getElementById('priceListFileC').addEventListener('change', function(e) {
  loadFileIntoTextarea(e.target.files[0], 'completenessBox');
});

// -- Catalog matching file inputs -------------------------------------
document.getElementById('priceListFileItems').addEventListener('change', function(e) {
  var f = e.target.files[0]; if (!f) return;
  loadStructuredFile(f, function(items, fname) {
    _cmpQueryItems = items;
    updateCatalogZoneLabel('itemsFileLabel', '✓ ' + items.length + ' позиций — <b>' + fname + '</b>');
    showToast('Загружено ' + items.length + ' позиций из «' + fname + '»');
  });
});
document.getElementById('priceListFileCatalog').addEventListener('change', function(e) {
  var f = e.target.files[0]; if (!f) return;
  loadStructuredFile(f, function(items, fname) {
    _cmpCatalogItems = items;
    updateCatalogZoneLabel('catalogFileLabel', '✓ ' + items.length + ' позиций — <b>' + fname + '</b>');
    showToast('Каталог: ' + items.length + ' позиций из «' + fname + '»');
  });
});
document.getElementById('runCatalogMatchBtn').addEventListener('click', function() {
  if (!_cmpQueryItems.length)   { showToast('Загрузите файл «Ваши позиции».'); return; }
  if (!_cmpCatalogItems.length) { showToast('Загрузите файл «Каталог».'); return; }
  renderCatalogMatch();
});

// -- Catalog Matcher API bridge (project-scoped growing DB) ------------
var _cmProjects = [];
var _cmSources = [];

function catalogMatcherBase() {
  var el = document.getElementById('catalogMatcherApiBase');
  var base = (el && el.value ? el.value : 'http://localhost:8000/api').replace(/\/$/, '');
  try { localStorage.setItem('catalogMatcher.apiBase', base); } catch (e) {}
  return base;
}

function setCatalogMatcherStatus(text, isError) {
  var el = document.getElementById('catalogMatcherApiStatus');
  if (!el) return;
  el.textContent = text || '';
  el.style.color = isError ? 'var(--red)' : 'var(--text-dim)';
}

function renderCatalogMatcherSourcesForProject(projectId) {
  var box = document.getElementById('catalogMatcherSourceList');
  if (!box) return;
  var project = _cmProjects.find(function(p) { return String(p.id) === String(projectId); });
  if (!project) {
    box.innerHTML = '<span style="color:var(--text-dim)">Нет выбранного проекта</span>';
    return;
  }
  var links = (project.catalog_links || []).filter(function(l) { return l.include_in_matching; });
  if (!links.length) {
    box.innerHTML = '<span style="color:var(--text-dim)">Нет включённых каталогов — настройте в Catalog Matcher → Проекты</span>';
    return;
  }
  box.innerHTML = links.map(function(l) {
    return '<span style="display:inline-block;margin:0 6px 6px 0;padding:3px 8px;border-radius:6px;background:#e2e8f0;font-weight:600;">'
      + (l.source_name || ('#' + l.source_id)) + '</span>';
  }).join('');
}

async function refreshCatalogMatcherBridge() {
  var base = catalogMatcherBase();
  var uiLink = document.getElementById('openCatalogMatcherUi');
  if (uiLink) {
    try {
      var u = new URL(base);
      uiLink.href = u.origin.replace(':8000', ':5173');
    } catch (e) {
      uiLink.href = 'http://localhost:5173';
    }
  }
  setCatalogMatcherStatus('Загрузка проектов…');
  try {
    var [projRes, srcRes] = await Promise.all([
      fetch(base + '/projects'),
      fetch(base + '/catalog-sources'),
    ]);
    if (!projRes.ok) throw new Error('projects HTTP ' + projRes.status);
    if (!srcRes.ok) throw new Error('sources HTTP ' + srcRes.status);
    _cmProjects = await projRes.json();
    _cmSources = await srcRes.json();
    var sel = document.getElementById('catalogMatcherProjectSelect');
    if (sel) {
      var prev = sel.value;
      sel.innerHTML = '<option value="">— выберите проект —</option>' +
        _cmProjects.map(function(p) {
          return '<option value="' + p.id + '">' + p.name + '</option>';
        }).join('');
      if (prev) sel.value = prev;
      renderCatalogMatcherSourcesForProject(sel.value);
    }
    setCatalogMatcherStatus(
      'API OK · проектов: ' + _cmProjects.length +
      ' · каталогов: ' + _cmSources.length +
      ' (enabled: ' + _cmSources.filter(function(s) { return s.is_enabled; }).length + ')'
    );
  } catch (err) {
    setCatalogMatcherStatus(
      'API недоступен (' + (err && err.message ? err.message : err) + '). Запустите catalog-matcher backend на :8000.',
      true
    );
  }
}

async function runCatalogMatcherForProject() {
  var sel = document.getElementById('catalogMatcherProjectSelect');
  var projectId = sel && sel.value ? Number(sel.value) : null;
  if (!projectId) {
    showToast('Выберите проект');
    return;
  }
  var base = catalogMatcherBase();
  setCatalogMatcherStatus('Запуск подбора для проекта #' + projectId + '…');
  try {
    var res = await fetch(base + '/match/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId,
        matching_mode: 'balanced',
        embed_catalog_if_missing: false,
      }),
    });
    var body = await res.json().catch(function() { return {}; });
    if (!res.ok) throw new Error(body.detail || ('HTTP ' + res.status));
    setCatalogMatcherStatus(
      'Подбор запущен · run #' + body.run_id +
      ' · источники: ' + ((body.source_names || []).join(', ') || '—') +
      '. Откройте UI для ревью.'
    );
    showToast('Подбор по проекту запущен (run #' + body.run_id + ')');
  } catch (err) {
    setCatalogMatcherStatus(String(err.message || err), true);
    showToast(String(err.message || err));
  }
}

(function initCatalogMatcherBridge() {
  var baseInput = document.getElementById('catalogMatcherApiBase');
  if (!baseInput) return;
  try {
    var saved = localStorage.getItem('catalogMatcher.apiBase');
    if (saved) baseInput.value = saved;
  } catch (e) {}
  var refreshBtn = document.getElementById('catalogMatcherRefreshBtn');
  var runBtn = document.getElementById('catalogMatcherRunProjectBtn');
  var sel = document.getElementById('catalogMatcherProjectSelect');
  if (refreshBtn) refreshBtn.addEventListener('click', refreshCatalogMatcherBridge);
  if (runBtn) runBtn.addEventListener('click', runCatalogMatcherForProject);
  if (sel) sel.addEventListener('change', function() {
    renderCatalogMatcherSourcesForProject(sel.value);
  });
  refreshCatalogMatcherBridge();
})();

// -- Drag-and-drop ----------------------------------------------------
[
  ['dropZoneA', 'compareBoxA'],
  ['dropZoneB', 'compareBoxB'],
  ['dropZoneC', 'completenessBox'],
].forEach(function(pair) {
  var zone = document.getElementById(pair[0]);
  var taId = pair[1];
  if (!zone) return;
  zone.addEventListener('dragover', function(e) {
    e.preventDefault();
    zone.style.borderColor = 'var(--blue)';
    zone.style.background  = '#eff6ff';
  });
  zone.addEventListener('dragleave', function() {
    zone.style.borderColor = '';
    zone.style.background  = '';
  });
  zone.addEventListener('drop', function(e) {
    e.preventDefault();
    zone.style.borderColor = '';
    zone.style.background  = '';
    var file = e.dataTransfer.files[0];
    if (!file) return;
    var ext = file.name.split('.').pop().toLowerCase();
    if (['xlsx','xls','csv'].indexOf(ext) !== -1) {
      loadFileIntoTextarea(file, taId);
    } else {
      showToast('Поддерживаются только .xlsx, .xls, .csv');
    }
  });
});

// Catalog matching zones — structured load (keeps code/brand/model)
[
  ['dropZoneItems',   function(items,fname){ _cmpQueryItems=items;   updateCatalogZoneLabel('itemsFileLabel',   '✓ '+items.length+' позиций — <b>'+fname+'</b>'); showToast('Загружено '+items.length+' позиций из «'+fname+'»'); }],
  ['dropZoneCatalog', function(items,fname){ _cmpCatalogItems=items; updateCatalogZoneLabel('catalogFileLabel', '✓ '+items.length+' позиций — <b>'+fname+'</b>'); showToast('Каталог: '+items.length+' позиций из «'+fname+'»'); }],
].forEach(function(pair) {
  var zone=document.getElementById(pair[0]); var cb=pair[1];
  if (!zone) return;
  zone.addEventListener('dragover', function(e){ e.preventDefault(); zone.style.borderColor='var(--blue)'; zone.style.background='#eff6ff'; });
  zone.addEventListener('dragleave', function(){ zone.style.borderColor=''; zone.style.background=''; });
  zone.addEventListener('drop', function(e){
    e.preventDefault(); zone.style.borderColor=''; zone.style.background='';
    var file=e.dataTransfer.files[0]; if(!file) return;
    var ext=file.name.split('.').pop().toLowerCase();
    if(['xlsx','xls','csv'].indexOf(ext)!==-1) loadStructuredFile(file,cb);
    else showToast('Поддерживаются только .xlsx, .xls, .csv');
  });
});

// -- Tab switching ----------------------------------------------------
document.querySelectorAll('.cmp-tab').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var target = btn.dataset.cmpTab;
    document.querySelectorAll('.cmp-tab').forEach(function(b) {
      var active = b.dataset.cmpTab === target;
      b.style.borderBottomColor = active ? 'var(--blue)' : 'transparent';
      b.style.color = active ? 'var(--blue)' : 'var(--text-dim)';
    });
    document.getElementById('cmpTabMatch').style.display        = target === 'match'        ? '' : 'none';
    document.getElementById('cmpTabCompleteness').style.display = target === 'completeness' ? '' : 'none';
    document.getElementById('cmpTabCatalog').style.display      = target === 'catalog'      ? '' : 'none';
  });
});
