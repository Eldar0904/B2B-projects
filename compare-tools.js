/* =====================================================================
   COMPARE & COMPLETENESS TOOLS — rule-based, deterministic, no API calls.

   Three independent tools:
     1. Room-kit / spec comparison — diffs two pasted lists (only-in-A,
        only-in-B, quantity mismatches, matched, likely analogs).
     2. Spec completeness checker — keyword-classifies items into 15+
        broad categories and flags missing categories, lines without a
        recognized quantity, and duplicate item names.
     3. Supplier price-list import (Excel/CSV) — parses an uploaded
        spreadsheet client-side, guesses name/qty/price columns by header
        keywords, and feeds the result into tools 1 and 2.

   Matching upgraded with logic ported from catalog-matcher (Python):
     • cleanText()         ← normalize.py  clean_text()
       NFKC normalize, lowercase, strip non-alnum, collapse whitespace,
       Russian + English unit alias normalization.
     • nameSimilarity()   ← tfidf_retriever.py  (word 1-2gram + char 3-5gram)
       Replaced Levenshtein with hybrid Jaccard on word bigrams (55%) +
       character trigrams (45%) — same weights as the Python TF-IDF retriever.
       Much better on long Russian item names and word-order differences.

   No server calls, no model, no API key — fully deterministic.
   Loaded as <script src="compare-tools.js">; relies on `showToast` and
   the SheetJS (XLSX) global from the main HTML.
   ===================================================================== */

/* ─────────────────────── 0. Normalisation (ported from normalize.py) ─── */

// Russian and English unit aliases — fold into canonical form so
// "шт." / "шт" / "pcs" / "pc" are all the same token.
const UNIT_ALIASES = {
  // Russian
  'шт\\.?':'шт', 'штук':'шт', 'штуки':'шт',
  'кг\\.?':'кг', 'кило':'кг', 'килограмм':'кг',
  'г\\.?':'г', 'грамм':'г',
  'м\\.?':'м', 'метр':'м', 'метров':'м',
  'см\\.?':'см', 'сантиметр':'см',
  'мм\\.?':'мм', 'миллиметр':'мм',
  'л\\.?':'л', 'литр':'л',
  'компл\\.?':'компл', 'комплект':'компл',
  'упак\\.?':'упак', 'упаковк':'упак',
  'рул\\.?':'рул', 'рулон':'рул',
  // English
  '\\bpcs?\\.?\\b':'pcs', '\\bpc\\.?\\b':'pcs',
  '\\bkg\\.?\\b':'kg',
  '\\bcm\\.?\\b':'cm', '\\bmm\\.?\\b':'mm',
  '\\bm\\.?\\b':'m',
};

/**
 * Ported from normalize.py clean_text().
 * NFKC → lowercase → strip non-alnum (keeps Cyrillic) → collapse whitespace
 * → apply unit aliases.
 */
function cleanText(value) {
  if (value == null) return '';
  let t = String(value).trim();
  // NFKC normalization (resolves ligatures, full-width chars, etc.)
  t = t.normalize('NFKC');
  t = t.toLowerCase();
  // Strip anything that isn't a Cyrillic letter, Latin letter, digit, or space
  t = t.replace(/[^a-z0-9а-яёa-z\s]/gi, ' ');
  // Collapse whitespace
  t = t.replace(/\s+/g, ' ').trim();
  // Apply unit aliases
  for (const [pat, rep] of Object.entries(UNIT_ALIASES)) {
    t = t.replace(new RegExp(pat, 'g'), rep);
  }
  t = t.replace(/\s+/g, ' ').trim();
  return t;
}

// Strict key used for exact-match grouping (same as before)
function normalizeItemName(name) {
  return cleanText(name)
    .replace(/[.,;:]+$/, '')
    .trim();
}

// "Школьные парты (2-местные) x120" → { raw, name, qty, key }
function parseListLine(line) {
  const raw = line.trim();
  const m = raw.match(/^(.*?)(?:\s*[xх]\s*(\d+(?:[.,]\d+)?))\s*$/i);
  const name = (m ? m[1] : raw).trim();
  const qty = m && m[2] ? Number(m[2].replace(',', '.')) : null;
  return { raw, name, qty, key: normalizeItemName(name) };
}

function parseList(rawText) {
  return rawText.split('\n').map(l => l.trim()).filter(Boolean).map(parseListLine);
}

/* ─────────────────────── 1. Hybrid similarity (ported from tfidf_retriever) ─ */

/**
 * Build a Set of character n-grams of length n from a string.
 * Pads with '#' so edge n-grams are captured (same approach as
 * scikit-learn's analyzer='char_wb').
 */
function charNgrams(str, n) {
  const s = `${'#'.repeat(n - 1)}${str}${'#'.repeat(n - 1)}`;
  const ng = new Set();
  for (let i = 0; i <= s.length - n; i++) ng.add(s.slice(i, i + n));
  return ng;
}

/**
 * Build a Set of word unigrams + bigrams from a token array.
 * Mirrors scikit-learn TfidfVectorizer(ngram_range=(1,2), analyzer='word').
 */
function wordNgrams(tokens) {
  const ng = new Set(tokens);
  for (let i = 0; i < tokens.length - 1; i++) ng.add(`${tokens[i]} ${tokens[i + 1]}`);
  return ng;
}

/** Jaccard similarity between two Sets. */
function jaccard(a, b) {
  if (!a.size && !b.size) return 0;
  let inter = 0;
  a.forEach(v => { if (b.has(v)) inter++; });
  return inter / (a.size + b.size - inter);
}

/**
 * Minimal Russian suffix normalizer — strips the most common adjective and
 * noun inflection endings so that "местные" and "местная", "ученический" and
 * "ученических" collapse to the same stem for word-token scoring.
 * Does NOT touch short words or canonical aliases to avoid over-stripping.
 */
const _RU_SUFFIXES = [
  // longest first so "школьного" strips "ного" not "го"
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

/**
 * Hybrid similarity: 55% word-bigram Jaccard + 45% char-trigram Jaccard.
 * Weights match the Python TF-IDF retriever (word 1-2gram 55%, char 3-5gram 45%).
 * Dramatically better than Levenshtein on long Russian item names and
 * word-order differences ("парта школьная" vs "школьная парта").
 *
 * Pipeline:
 *   cleanText → applyAliases (for word scoring) / keep cleaned (for char scoring)
 * This way synonyms collapse on the word axis while morphological overlap
 * is still captured on the char axis.
 */
function nameSimilarity(a, b) {
  const ka = cleanText(a), kb = cleanText(b);
  if (!ka || !kb) return 0;
  if (ka === kb) return 1;

  // Alias-canonicalized versions for word n-gram scoring
  const aa = applyAliases(ka), ab = applyAliases(kb);
  // stemRu collapses Russian adjective/noun inflections ("местные"→"местн")
  // sort() makes comparison order-invariant ("парта школьная" = "школьная парта")
  const tokA = aa.split(' ').filter(Boolean).map(stemRu).sort();
  const tokB = ab.split(' ').filter(Boolean).map(stemRu).sort();

  // Word unigrams + bigrams (on synonym-canonicalized, order-sorted tokens)
  const wordScore = jaccard(wordNgrams(tokA), wordNgrams(tokB));

  // Char 3-grams on raw cleaned text (captures morphological overlap like
  // "учени" shared between "ученический" and "учеников")
  const charScore = jaccard(charNgrams(ka, 3), charNgrams(kb, 3));

  return wordScore * 0.55 + charScore * 0.45;
}

/* ─────────────────────── 2. Known synonym pairs ─────────────────────────── */

// Applied before similarity scoring to collapse well-known alternate phrasings,
// including common Russian plural / adjective morphological variants.
const SYNONYM_PAIRS = [
  [['интерактивная доска','доска интерактивная','умная доска','смарт доска','смарт-доска',
     'интерактивные доски','доски интерактивные'],'интердоска'],
  [['документ-камера','документ камера','визуализатор','докум камера','документ-камеры'],'докками'],
  [['школьная парта','парта школьная','парты школьные','школьные парты',
     'парта ученическая','ученическая парта','парты ученические',
     'ученический стол','стол ученический','столы ученические'],'парта'],
  [['ученический стул','стул ученический','стулья ученические','ученические стулья',
     'стул для учеников','стулья для учеников','стулья для класса','стул для класса'],'стул учен'],
  [['мультимедийный проектор','проектор мультимедийный','мультимедиа проектор'],'проектор'],
  [['компьютер моноблок','моноблок компьютер','персональный компьютер'],'моноблок'],
  [['акустическая система','акустические системы','колонки активные','активные колонки',
     'акустика активная'],'акустика'],
  [['учительский стол','стол учителя','стол педагога','рабочий стол учителя'],'стол учителя'],
  [['шкаф для одежды','шкаф гардеробный','гардеробный шкаф','шкаф для учеников',
     'шкаф ученический','шкафы для одежды'],'шкаф гардероб'],
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
  const t = applyAliases(normalizeItemName(name));
  return t.split(' ').filter(Boolean).sort().join(' ');
}

/* ─────────────────────── 3. Comparison / diff ───────────────────────────── */

const FUZZY_MATCH_THRESHOLD = 0.32; // hybrid scorer — 0.32 reliably catches same-item rewording

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

  // Second pass: fuzzy analog matching among unmatched items.
  // Uses hybrid word-bigram + char-trigram Jaccard (ported from catalog-matcher).
  const likely = [];
  const usedBKeys = new Set();
  const stillOnlyA = [];
  onlyA.forEach(itA => {
    let best = null, bestScore = 0;
    onlyB.forEach(itB => {
      if (usedBKeys.has(itB.key)) return;
      const score = nameSimilarity(itA.name, itB.name);
      if (score > bestScore) { bestScore = score; best = itB; }
    });
    if (best && bestScore >= FUZZY_MATCH_THRESHOLD) {
      likely.push({ nameA: itA.name, nameB: best.name, qtyA: itA.qty, qtyB: best.qty, score: bestScore });
      usedBKeys.add(best.key);
    } else {
      stillOnlyA.push(itA);
    }
  });
  const stillOnlyB = onlyB.filter(itB => !usedBKeys.has(itB.key));

  return { onlyA: stillOnlyA, onlyB: stillOnlyB, mismatched, matched, likely };
}

function renderCompareResults(diff) {
  const wrap = document.getElementById('compareResultsWrap');
  const qtyLabel = q => q === null ? '—' : q;
  const section = (title, badgeClass, rows) => rows.length ? `
    <div class="result-block">
      <div class="result-head"><b>${title}</b><span class="badge ${badgeClass}">${rows.length}</span></div>
      <div style="padding:12px 16px;">${rows}</div>
    </div>` : '';

  const likelyRows = diff.likely.map(it =>
    `<div class="item-row"><span class="item-name">${it.nameA} <span style="color:var(--text-dim);font-weight:400;">≈</span> ${it.nameB}</span><span class="qty">${Math.round(it.score * 100)}% похоже</span></div>`
  ).join('');
  const onlyARows = diff.onlyA.map(it =>
    `<div class="item-row"><span class="item-name">${it.name}</span><span class="qty">${qtyLabel(it.qty)}</span></div>`
  ).join('');
  const onlyBRows = diff.onlyB.map(it =>
    `<div class="item-row"><span class="item-name">${it.name}</span><span class="qty">${qtyLabel(it.qty)}</span></div>`
  ).join('');
  const mismatchRows = diff.mismatched.map(it =>
    `<div class="item-row"><span class="item-name">${it.name}</span><span class="qty">А: ${qtyLabel(it.qtyA)} → Б: ${qtyLabel(it.qtyB)}</span></div>`
  ).join('');

  let html = `<div class="card-title" style="margin-bottom:10px;">Результаты сравнения <span class="demo-flag" style="background:#f2f4f7;color:var(--text-dim);">слово-биграмм + символьный триграмм · без внешних сервисов</span></div>`;
  html += section('Вероятно один и тот же товар (другая формулировка)', 'badge-blue', likelyRows);
  html += section('Только в комплектации А', 'badge-red', onlyARows);
  html += section('Только в комплектации Б', 'badge-green', onlyBRows);
  html += section('Совпадают по наименованию, но разное количество', 'badge-amber', mismatchRows);
  if (!diff.onlyA.length && !diff.onlyB.length && !diff.mismatched.length && !diff.likely.length) {
    html += `<div class="card" style="border-color:var(--green);background:var(--green-bg);color:var(--green);font-weight:600;font-size:13px;">Расхождений не найдено — ${diff.matched.length} позиций совпадают полностью.</div>`;
  }
  wrap.innerHTML = html;
}

document.getElementById('runCompareBtn').addEventListener('click', () => {
  const rawA = document.getElementById('compareBoxA').value.trim();
  const rawB = document.getElementById('compareBoxB').value.trim();
  if (!rawA || !rawB) { showToast('Вставьте обе комплектации (А и Б) перед сравнением.'); return; }
  const diff = diffLists(parseList(rawA), parseList(rawB));
  renderCompareResults(diff);
});

/* ─────────────────────── 4. Completeness checker ────────────────────────── */

// Expanded from 3 to 15 categories — covers real school/cultural VOR content.
// Keywords are substrings matched against cleanText(name).
const CATEGORY_KEYWORDS = {
  'Мебель':         ['парт','стул','стол','шкаф','стеллаж','кресл','диван','тумб','скамь','шкаф','вешалк','полк','стенд'],
  'Техника':        ['доск','камер','проектор','экран','ноутбук','компьютер','моноблок','монитор','принтер','колонк','акустик','микрофон','усилител','пульт','зарядн','роутер','сервер','планшет','wi-fi','wifi','телевизор','тв ','апмп','blu-ray','ресивер','источник питан'],
  'Музыкальные инструменты': ['рояль','пианино','фортепиан','скрипк','виолончел','альт','контрабас','флейт','гобой','кларнет','фаготт','валторн','труб','тромбон','туб','балалайк','домр','гитар','баян','аккордеон','орган','синтезатор','барабан','ксилофон','маримб','ударн'],
  'Осветительное оборудование': ['прожектор','светильник','люстр','лампа','диммер','прожект','led','светодиод','светол','трек','прожект','рампа','подсветк'],
  'Сценическое оборудование':   ['занавес','кулис','задник','штанкет','фонарь сцен','микшер','микшерн','пульт звук','акустическ система','монитор сцен','диммер','стойк'],
  'Климат и вентиляция':        ['кондиционер','сплит','вентилятор','тепловентилятор','обогреватель','рекуператор','вентиляц','увлажнитель'],
  'Безопасность':               ['камера видеонаблюд','видеонаблюдение','видеокамера','огнетушитель','пожарн','турникет','замок','электрозамок','кнопка вызов','сигнализац','видеодомофон'],
  'Дидактика':                  ['учебник','плакат','наглядн','методич','нотн','ноты','пособие','карточк','раздаточ','дидактич','таблиц','атлас','словарь','книга','литератур'],
  'Спортивное оборудование':    ['спортивн','брусья','шведск','матер','мяч','кольц','ворот','тренажер','гантел','штанг','гимнастическ','секундомер'],
  'Лабораторное оборудование':  ['микроскоп','лупа','пробирк','реактив','лаборатор','штатив','весы','мензурк','химическ'],
  'Библиотека':                 ['стеллаж книг','библиотечн','картотек','читальн','выставочн витрин','выставочн стенд'],
  'Уборочный инвентарь':        ['швабр','ведр','тряпк','щётк','совок','уборочн','пылесос','мойк'],
  'Хозяйственный инвентарь':    ['хозяйственн','стремянк','лестниц','тележк','инструмент','отвёртк','молоток','дрел','шуруповёрт'],
  'Посуда и кухня':             ['посуд','тарелк','кружк','стакан','чайник','кофемашин','микроволн','холодильник','водонагреватель','диспенсер','кулер'],
  'Прочее':                     [],   // catch-all — assigned when nothing else matches
};

// Categories that must be present in a complete school fitout spec.
// If any are missing, the checker flags them.
const REQUIRED_CATEGORIES = ['Мебель', 'Техника', 'Дидактика'];

function classifyItem(name) {
  const n = cleanText(name);
  const matches = Object.keys(CATEGORY_KEYWORDS).filter(cat => {
    if (cat === 'Прочее') return false;
    return CATEGORY_KEYWORDS[cat].some(kw => n.includes(kw));
  });
  return matches.length ? matches : ['Прочее'];
}

function checkCompleteness(items) {
  const categoriesFound = new Set();
  items.forEach(it => classifyItem(it.name).forEach(c => categoriesFound.add(c)));

  const missingCategories = REQUIRED_CATEGORIES.filter(c => !categoriesFound.has(c));

  const noQty = items.filter(it => it.qty === null);

  const seen = new Map();
  items.forEach(it => seen.set(it.key, (seen.get(it.key) || 0) + 1));
  const duplicates = [...seen.entries()]
    .filter(([, count]) => count > 1)
    .map(([key, count]) => ({ name: items.find(it => it.key === key).name, count }));

  // Category breakdown — for info panel
  const breakdown = {};
  items.forEach(it => {
    classifyItem(it.name).forEach(c => {
      breakdown[c] = (breakdown[c] || 0) + 1;
    });
  });

  return { missingCategories, noQty, duplicates, breakdown };
}

function renderCompletenessResults(result, totalItems) {
  const wrap = document.getElementById('completenessResultsWrap');
  let html = `<div class="card-title" style="margin-bottom:10px;">Результаты проверки <span class="demo-flag" style="background:#f2f4f7;color:var(--text-dim);">15 категорий · ключевые слова · без внешних сервисов</span></div>`;

  // Category breakdown pill row
  const sorted = Object.entries(result.breakdown).sort((a, b) => b[1] - a[1]);
  if (sorted.length) {
    const pills = sorted.map(([cat, cnt]) =>
      `<span style="display:inline-flex;align-items:center;gap:4px;background:var(--bg);border:1px solid var(--border);border-radius:20px;padding:3px 10px;font-size:11.5px;margin:3px 3px 3px 0;">
        <b style="color:var(--navy);">${cat}</b><span style="color:var(--text-dim);">${cnt} поз.</span>
      </span>`
    ).join('');
    html += `<div class="result-block" style="margin-bottom:14px;">
      <div class="result-head"><b>Распределение по категориям</b><span class="badge" style="background:#f1f5f9;color:var(--text-dim);">${sorted.length}</span></div>
      <div style="padding:10px 16px;">${pills}</div>
    </div>`;
  }

  if (result.missingCategories.length) {
    html += `<div class="result-block"><div class="result-head"><b>Обязательные категории без совпадений</b><span class="badge badge-amber">${result.missingCategories.length}</span></div>
      <div style="padding:12px 16px;font-size:13px;color:var(--text-dim);">Проверьте, что это не пропуск: ${result.missingCategories.join(', ')}.</div></div>`;
  }
  if (result.noQty.length) {
    html += `<div class="result-block"><div class="result-head"><b>Позиции без распознанного количества</b><span class="badge badge-gray">${result.noQty.length}</span></div>
      <div style="padding:12px 16px;">${result.noQty.map(it =>
        `<div class="item-row"><span class="item-name">${it.name}</span><span class="qty">— добавьте "x&lt;число&gt;"</span></div>`
      ).join('')}</div></div>`;
  }
  if (result.duplicates.length) {
    html += `<div class="result-block"><div class="result-head"><b>Повторяющиеся наименования</b><span class="badge badge-red">${result.duplicates.length}</span></div>
      <div style="padding:12px 16px;">${result.duplicates.map(d =>
        `<div class="item-row"><span class="item-name">${d.name}</span><span class="qty">встречается ${d.count} раза</span></div>`
      ).join('')}</div></div>`;
  }
  if (!result.missingCategories.length && !result.noQty.length && !result.duplicates.length) {
    html += `<div class="card" style="border-color:var(--green);background:var(--green-bg);color:var(--green);font-weight:600;font-size:13px;">Замечаний не найдено по ${totalItems} позициям (категории, количество, дубликаты).</div>`;
  }
  wrap.innerHTML = html;
}

document.getElementById('runCompletenessBtn').addEventListener('click', () => {
  const raw = document.getElementById('completenessBox').value.trim();
  if (!raw) { showToast('Вставьте перечень позиций перед проверкой.'); return; }
  const items = parseList(raw);
  const result = checkCompleteness(items);
  renderCompletenessResults(result, items.length);
});

/* ─────────────────────── 5. Supplier price-list import (Excel / CSV) ─────── */

const PRICE_LIST_HEADER_HINTS = {
  name:  ['наименован','назван','товар','позици','номенклатур','item','name','description','наим'],
  qty:   ['кол-во','количество','шт','qty','quantity','штук','объем','кол во'],
  price: ['цена','стоимост','price','сумма','тариф','cost','rate'],
};

function guessColumnIndexes(headerRow) {
  const lower = headerRow.map(h => String(h || '').toLowerCase().trim());
  const findCol = hints => {
    for (let i = 0; i < lower.length; i++) {
      if (hints.some(h => lower[i].includes(h))) return i;
    }
    return -1;
  };
  return {
    name:  findCol(PRICE_LIST_HEADER_HINTS.name),
    qty:   findCol(PRICE_LIST_HEADER_HINTS.qty),
    price: findCol(PRICE_LIST_HEADER_HINTS.price),
  };
}

function parseMoneyValue(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  if (typeof raw === 'number') return raw;
  const cleaned = String(raw).replace(/[^\d.,-]/g, '').replace(',', '.');
  return cleaned && !isNaN(Number(cleaned)) ? Number(cleaned) : null;
}

function rowsFromSheet(sheetRows) {
  const nonEmpty = sheetRows.filter(r => r.some(c => String(c || '').trim() !== ''));
  if (!nonEmpty.length) return { rows: [], cols: null };

  let cols = guessColumnIndexes(nonEmpty[0]);
  let dataStart = 1;
  if (cols.name === -1) {
    cols = { name: 0, qty: -1, price: -1 };
    dataStart = 0;
  }

  const rows = nonEmpty.slice(dataStart).map(r => ({
    name:  String(r[cols.name]  || '').trim(),
    qty:   cols.qty   >= 0 ? parseMoneyValue(r[cols.qty])   : null,
    price: cols.price >= 0 ? parseMoneyValue(r[cols.price]) : null,
  })).filter(r => r.name);

  return { rows, cols };
}

function rowsToListText(rows) {
  return rows.map(r => r.qty != null ? `${r.name} x${r.qty}` : r.name).join('\n');
}

function renderPriceListPreview(rows, cols) {
  const wrap = document.getElementById('priceListPreview');
  if (!rows.length) {
    wrap.innerHTML = `<div class="card" style="border-color:var(--red);background:var(--red-bg);color:var(--red);font-weight:600;font-size:13px;">Не удалось распознать строки с наименованиями в этом файле.</div>`;
    return;
  }
  const mapNote = (cols && cols.name >= 0 && (cols.qty >= 0 || cols.price >= 0))
    ? `Определены колонки: наименование${cols.qty >= 0 ? ', количество' : ''}${cols.price >= 0 ? ', цена' : ''}.`
    : `Заголовки не распознаны — взята первая колонка как наименование, проверьте результат.`;

  const sampleRows = rows.slice(0, 300).map(r => `<tr>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border);">${r.name}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border);text-align:right;">${r.qty ?? '—'}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border);text-align:right;">${r.price != null ? r.price.toLocaleString('ru-RU') : '—'}</td>
    </tr>`).join('');

  wrap.innerHTML = `
    <div class="card-title" style="margin-bottom:8px;font-size:13px;">Распознано позиций: ${rows.length} <span class="demo-flag" style="background:#f2f4f7;color:var(--text-dim);">${mapNote}</span></div>
    <div style="max-height:280px;overflow:auto;border:1px solid var(--border);border-radius:10px;margin-bottom:12px;">
      <table style="width:100%;border-collapse:collapse;font-size:12.5px;">
        <thead><tr style="background:var(--bg);position:sticky;top:0;">
          <th style="text-align:left;padding:8px 10px;">Наименование</th>
          <th style="text-align:right;padding:8px 10px;">Кол-во</th>
          <th style="text-align:right;padding:8px 10px;">Цена</th>
        </tr></thead>
        <tbody>${sampleRows}</tbody>
      </table>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <button class="btn" id="pushPriceListA">Вставить в Комплектацию А</button>
      <button class="btn" id="pushPriceListB">Вставить в Комплектацию Б</button>
      <button class="btn" id="pushPriceListCompleteness">Вставить в проверку полноты</button>
    </div>`;

  document.getElementById('pushPriceListA').addEventListener('click', () => {
    document.getElementById('compareBoxA').value = rowsToListText(rows);
    showToast('Список вставлен в Комплектацию А.');
  });
  document.getElementById('pushPriceListB').addEventListener('click', () => {
    document.getElementById('compareBoxB').value = rowsToListText(rows);
    showToast('Список вставлен в Комплектацию Б.');
  });
  document.getElementById('pushPriceListCompleteness').addEventListener('click', () => {
    document.getElementById('completenessBox').value = rowsToListText(rows);
    showToast('Список вставлен в проверку полноты.');
  });
}

document.getElementById('priceListFile').addEventListener('change', e => {
  const file = e.target.files[0];
  if (!file) return;
  if (typeof XLSX === 'undefined') {
    showToast('Не удалось загрузить библиотеку для чтения файла. Проверьте подключение и обновите страницу.');
    return;
  }
  const ext = file.name.split('.').pop().toLowerCase();
  const reader = new FileReader();
  reader.onload = ev => {
    try {
      const wb = ext === 'csv'
        ? XLSX.read(ev.target.result, { type: 'binary' })
        : XLSX.read(new Uint8Array(ev.target.result), { type: 'array' });
      const ws = wb.Sheets[wb.SheetNames[0]];
      const sheetRows = XLSX.utils.sheet_to_json(ws, { header: 1, raw: true, defval: '' });
      const { rows, cols } = rowsFromSheet(sheetRows);
      renderPriceListPreview(rows, cols);
    } catch (err) {
      showToast('Не удалось прочитать файл: ' + err.message);
    }
  };
  reader.onerror = () => showToast('Не удалось прочитать файл.');
  if (ext === 'csv') reader.readAsBinaryString(file);
  else reader.readAsArrayBuffer(file);
});
