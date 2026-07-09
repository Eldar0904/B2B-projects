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
  const raw = line.trim();
  const m = raw.match(/^(.*?)(?:\s*[xх]\s*(\d+(?:[.,]\d+)?))\s*$/i);
  const name = (m ? m[1] : raw).trim();
  const qty  = m && m[2] ? Number(m[2].replace(',', '.')) : null;
  return { raw, name, qty, key: normalizeItemName(name), code: null };
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

function matchItem(queryItem, catalog) {
  const candidates = retrieve(queryItem, catalog);
  const qCode = ((queryItem.code || '')).trim().toUpperCase();
  if (qCode) {
    const inPool = new Set(candidates.map(c => c.key));
    catalog.forEach(catItem => {
      if (inPool.has(catItem.key)) return;
      const cCode = ((catItem.code || '')).trim().toUpperCase();
      if (!cCode) return;
      if (qCode === cCode || codeRatio(qCode, cCode) > 0.8) {
        candidates.push(makeCandidate(catItem, 0, 'код совпадение', false));
      }
    });
  }
  const filtered = deterministicFilter(queryItem, candidates);
  if (!filtered.length) return null;
  return filtered.sort((a, b) => b.score - a.score)[0];
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
  var rawA = document.getElementById('compareBoxA').value.trim();
  var rawB = document.getElementById('compareBoxB').value.trim();
  if (!rawA || !rawB) { showToast('Вставьте обе комплектации (А и Б) перед сравнением.'); return; }
  renderCompareResults(diffLists(parseList(rawA), parseList(rawB)));
});


/* -------------------------------------------------------------------------
   7. COMPLETENESS CHECKER  (tool 2)
   ---------------------------------------------------------------------- */

const CATEGORY_KEYWORDS = {
  'Мебель':                     ['парт','стул','стол','шкаф','стеллаж','кресл','диван','тумб','скамь','вешалк','полк','стенд'],
  'Техника':                    ['доск','камер','проектор','экран','ноутбук','компьютер','моноблок','монитор','принтер','колонк','акустик','микрофон','усилител','пульт','зарядн','роутер','сервер','планшет','wifi','телевизор','blu-ray','ресивер'],
  'Музыкальные инструменты':    ['рояль','пианино','фортепиан','скрипк','виолончел','альт','контрабас','флейт','гобой','кларнет','фаготт','валторн','труб','тромбон','туб','балалайк','домр','гитар','баян','аккордеон','орган','синтезатор','барабан','ксилофон','маримб','ударн'],
  'Осветительное оборудование': ['прожектор','светильник','люстр','лампа','диммер','led','светодиод','трек','рампа','подсветк'],
  'Сценическое оборудование':   ['занавес','кулис','задник','штанкет','микшер','микшерн','монитор сцен','стойк'],
  'Климат и вентиляция':        ['кондиционер','сплит','вентилятор','тепловентилятор','обогреватель','рекуператор','вентиляц','увлажнитель'],
  'Безопасность':               ['видеонаблюд','видеокамера','огнетушитель','пожарн','турникет','замок','электрозамок','кнопка вызов','сигнализац','видеодомофон'],
  'Дидактика':                  ['учебник','плакат','наглядн','методич','нотн','ноты','пособие','карточк','раздаточ','дидактич','таблиц','атлас','словарь','книга','литератур'],
  'Спортивное оборудование':    ['спортивн','брусья','шведск','матер','мяч','кольц','ворот','тренажер','гантел','штанг','гимнастическ','секундомер'],
  'Лабораторное оборудование':  ['микроскоп','лупа','пробирк','реактив','лаборатор','штатив','весы','мензурк','химическ'],
  'Библиотека':                 ['стеллаж книг','библиотечн','картотек','читальн','выставочн'],
  'Уборочный инвентарь':        ['швабр','ведр','тряпк','щётк','совок','уборочн','пылесос','мойк'],
  'Хозяйственный инвентарь':    ['хозяйственн','стремянк','лестниц','тележк','отвёртк','молоток','дрел','шуруповёрт'],
  'Посуда и кухня':             ['посуд','тарелк','кружк','стакан','чайник','кофемашин','микроволн','холодильник','водонагреватель','диспенсер','кулер'],
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
    + '<span class="demo-flag" style="background:#f2f4f7;color:var(--text-dim);">15 категорий · ключевые слова</span></div>';

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
      + result.noQty.map(function(it) { return '<div class="item-row"><span class="item-name">' + it.name + '</span><span class="qty">— добавьте "x&lt;число&gt;"</span></div>'; }).join('')
      + '</div></div>';
  }
  if (result.duplicates.length) {
    html += '<div class="result-block"><div class="result-head"><b>Повторяющиеся наименования</b><span class="badge badge-red">' + result.duplicates.length + '</span></div>'
      + '<div style="padding:12px 16px;">'
      + result.duplicates.map(function(d) { return '<div class="item-row"><span class="item-name">' + d.name + '</span><span class="qty">встречается ' + d.count + ' раза</span></div>'; }).join('')
      + '</div></div>';
  }
  if (!result.missingCategories.length && !result.noQty.length && !result.duplicates.length) {
 