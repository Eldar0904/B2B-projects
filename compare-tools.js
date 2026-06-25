/* =====================================================================
   COMPARE & COMPLETENESS TOOLS — rule-based, deterministic, no API calls.

   Three independent tools:
     1. Room-kit / spec comparison — diffs two pasted lists (only-in-A,
        only-in-B, quantity mismatches, matched, *likely analogs*), like
        a text diff with fuzzy matching on top.
     2. Spec completeness checker — keyword-classifies items into
        broad categories (Техника / Мебель / Дидактика) and flags
        categories with zero matches, lines with no recognized
        quantity, and duplicate item names.
     3. Supplier price-list import (Excel/CSV) — parses an uploaded
        spreadsheet client-side, guesses the name/qty/price columns by
        header keywords, and feeds the result into tools 1 and 2.

   This is intentionally NOT generative/LLM-based. Matching is exact
   string normalization + keyword substring checks, plus one classical
   algorithm (Levenshtein edit distance) for fuzzy/near-duplicate name
   matching — no model, no API key, fully deterministic and reproducible.
   Loaded as a plain classic <script src="compare-tools.js"></script>,
   shares global scope with the main inline script (relies on showToast
   already being defined there). The price-list importer additionally
   relies on the SheetJS (xlsx) library loaded via <script src> before
   this file. PDF price lists are NOT supported — reliable table
   extraction from PDF needs server-side tooling we don't have yet;
   users are told to save as Excel/CSV or paste text manually instead.
   ===================================================================== */

/* ----------------------- shared parsing helpers ----------------------- */
function normalizeItemName(name){
  return name
    .toLowerCase()
    .trim()
    .replace(/["'«»]/g, '')
    .replace(/\s+/g, ' ')
    .replace(/[.,;:]+$/, '');
}

// "Школьные парты (2-местные) x120" -> { raw, name, qty: 120, key }
function parseListLine(line){
  const raw = line.trim();
  const m = raw.match(/^(.*?)(?:\s*[xх]\s*(\d+))?\s*$/i);
  const name = (m ? m[1] : raw).trim();
  const qty = m && m[2] ? Number(m[2]) : null;
  return { raw, name, qty, key: normalizeItemName(name) };
}

function parseList(rawText){
  return rawText.split('\n').map(l=>l.trim()).filter(Boolean).map(parseListLine);
}

/* ----------------------- fuzzy matching helpers ----------------------- */
// Known phrasing variants that should be treated as the same item when
// fuzzy-matching (not used for exact-match dedupe, only for analog search).
const SYNONYM_MAP = {
  'интерактивная доска':'интердоска', 'доска интерактивная':'интердоска',
  'умная доска':'интердоска', 'смарт доска':'интердоска', 'смарт-доска':'интердоска',
  'документ-камера':'докками', 'документ камера':'докками', 'визуализатор':'докками',
  'школьная парта':'парта', 'парта школьная':'парта', 'парта ученическая':'парта',
  'ученический стол':'парта', 'стол ученический':'парта',
  'ученический стул':'стул', 'стул ученический':'стул',
  'мультимедийный проектор':'проектор', 'проектор мультимедийный':'проектор',
  'компьютер моноблок':'моноблок', 'моноблок компьютер':'моноблок',
};

// Token-sorted, synonym-canonicalized key used only for similarity scoring —
// ignores word order and known phrasing variants ("доска интерактивная" vs
// "интерактивная доска"). normalizeItemName() above stays the strict key
// used for exact-match dedupe/diff so existing behavior doesn't change.
function fuzzyKey(name){
  let n = normalizeItemName(name);
  Object.keys(SYNONYM_MAP).forEach(phrase=>{
    if(n.includes(phrase)) n = n.split(phrase).join(SYNONYM_MAP[phrase]);
  });
  return n.split(' ').filter(Boolean).sort().join(' ');
}

// Classic iterative Levenshtein edit distance — no library needed.
function levenshtein(a, b){
  const al = a.length, bl = b.length;
  if(al===0) return bl;
  if(bl===0) return al;
  let prev = Array.from({length:bl+1}, (_,j)=>j);
  for(let i=1;i<=al;i++){
    const cur = [i];
    for(let j=1;j<=bl;j++){
      const cost = a[i-1]===b[j-1] ? 0 : 1;
      cur[j] = Math.min(prev[j]+1, cur[j-1]+1, prev[j-1]+cost);
    }
    prev = cur;
  }
  return prev[bl];
}

// 0..1 similarity score between two item names (1 = identical after
// normalization, 0 = nothing in common).
function nameSimilarity(a, b){
  const ka = fuzzyKey(a), kb = fuzzyKey(b);
  if(!ka || !kb) return 0;
  if(ka===kb) return 1;
  const dist = levenshtein(ka, kb);
  return 1 - dist / Math.max(ka.length, kb.length);
}

/* ----------------------- 1. comparison / diff ----------------------- */
const FUZZY_MATCH_THRESHOLD = 0.72;

function diffLists(itemsA, itemsB){
  const mapA = new Map(itemsA.map(it=>[it.key, it]));
  const mapB = new Map(itemsB.map(it=>[it.key, it]));
  const onlyA = [], onlyB = [], mismatched = [], matched = [];

  mapA.forEach((itA, key)=>{
    if(!mapB.has(key)){ onlyA.push(itA); return; }
    const itB = mapB.get(key);
    if((itA.qty ?? null) !== (itB.qty ?? null)){
      mismatched.push({ name: itA.name, qtyA: itA.qty, qtyB: itB.qty });
    } else {
      matched.push(itA);
    }
  });
  mapB.forEach((itB, key)=>{ if(!mapA.has(key)) onlyB.push(itB); });

  // Second pass: among items that didn't exact-match, look for likely
  // analogs (same item, different wording — typos, word order, synonyms).
  // Greedy best-match, no item used twice.
  const likely = [];
  const usedBKeys = new Set();
  const stillOnlyA = [];
  onlyA.forEach(itA=>{
    let best = null, bestScore = 0;
    onlyB.forEach(itB=>{
      if(usedBKeys.has(itB.key)) return;
      const score = nameSimilarity(itA.name, itB.name);
      if(score > bestScore){ bestScore = score; best = itB; }
    });
    if(best && bestScore >= FUZZY_MATCH_THRESHOLD){
      likely.push({ nameA: itA.name, nameB: best.name, qtyA: itA.qty, qtyB: best.qty, score: bestScore });
      usedBKeys.add(best.key);
    } else {
      stillOnlyA.push(itA);
    }
  });
  const stillOnlyB = onlyB.filter(itB=>!usedBKeys.has(itB.key));

  return { onlyA: stillOnlyA, onlyB: stillOnlyB, mismatched, matched, likely };
}

function renderCompareResults(diff){
  const wrap = document.getElementById('compareResultsWrap');
  const qtyLabel = q => q===null ? '—' : q;
  const section = (title, badgeClass, rows) => rows.length ? `
    <div class="result-block">
      <div class="result-head"><b>${title}</b><span class="badge ${badgeClass}">${rows.length}</span></div>
      <div style="padding:12px 16px;">${rows}</div>
    </div>` : '';

  const likelyRows = diff.likely.map(it=>`<div class="item-row"><span class="item-name">${it.nameA} <span style="color:var(--text-dim);font-weight:400;">≈</span> ${it.nameB}</span><span class="qty">${Math.round(it.score*100)}% похоже</span></div>`).join('');
  const onlyARows = diff.onlyA.map(it=>`<div class="item-row"><span class="item-name">${it.name}</span><span class="qty">${qtyLabel(it.qty)}</span></div>`).join('');
  const onlyBRows = diff.onlyB.map(it=>`<div class="item-row"><span class="item-name">${it.name}</span><span class="qty">${qtyLabel(it.qty)}</span></div>`).join('');
  const mismatchRows = diff.mismatched.map(it=>`<div class="item-row"><span class="item-name">${it.name}</span><span class="qty">А: ${qtyLabel(it.qtyA)} → Б: ${qtyLabel(it.qtyB)}</span></div>`).join('');

  let html = `<div class="card-title" style="margin-bottom:10px;">Результаты сравнения <span class="demo-flag" style="background:#f2f4f7;color:var(--text-dim);">текстовое сравнение, без внешних сервисов</span></div>`;
  html += section('Вероятно один и тот же товар (другая формулировка)', 'badge-blue', likelyRows);
  html += section('Только в комплектации А', 'badge-red', onlyARows);
  html += section('Только в комплектации Б', 'badge-green', onlyBRows);
  html += section('Совпадают по наименованию, но разное количество', 'badge-amber', mismatchRows);
  if(!diff.onlyA.length && !diff.onlyB.length && !diff.mismatched.length && !diff.likely.length){
    html += `<div class="card" style="border-color:var(--green);background:var(--green-bg);color:var(--green);font-weight:600;font-size:13px;">Расхождений не найдено — ${diff.matched.length} позиций совпадают полностью.</div>`;
  }
  wrap.innerHTML = html;
}

document.getElementById('runCompareBtn').addEventListener('click', ()=>{
  const rawA = document.getElementById('compareBoxA').value.trim();
  const rawB = document.getElementById('compareBoxB').value.trim();
  if(!rawA || !rawB){ showToast('Вставьте обе комплектации (А и Б) перед сравнением.'); return; }
  const diff = diffLists(parseList(rawA), parseList(rawB));
  renderCompareResults(diff);
});

/* ----------------------- 2. completeness checker ----------------------- */
const CATEGORY_KEYWORDS = {
  'Техника': ['доск','камер','проектор','экран','ноутбук','компьютер','моноблок','монитор','принтер','колонк','микрофон','усилител','пульт','зарядн','роутер','сервер','планшет','wi-fi'],
  'Мебель': ['парт','стул','стол','шкаф','стеллаж','кресл','диван','тумб'],
  'Дидактика': ['учебник','плакат','наглядн','методич','нотн','ноты','пособие','карточк','раздаточ'],
};

function classifyItem(name){
  const n = name.toLowerCase();
  const matches = Object.keys(CATEGORY_KEYWORDS).filter(cat=>
    CATEGORY_KEYWORDS[cat].some(kw=>n.includes(kw))
  );
  return matches.length ? matches : ['Прочее'];
}

function checkCompleteness(items){
  const categoriesFound = new Set();
  items.forEach(it=>classifyItem(it.name).forEach(c=>categoriesFound.add(c)));
  const missingCategories = Object.keys(CATEGORY_KEYWORDS).filter(c=>!categoriesFound.has(c));

  const noQty = items.filter(it=>it.qty===null);

  const seen = new Map();
  items.forEach(it=>seen.set(it.key, (seen.get(it.key)||0)+1));
  const duplicates = [...seen.entries()].filter(([,count])=>count>1).map(([key,count])=>{
    const example = items.find(it=>it.key===key);
    return { name: example.name, count };
  });

  return { missingCategories, noQty, duplicates };
}

function renderCompletenessResults(result, totalItems){
  const wrap = document.getElementById('completenessResultsWrap');
  let html = `<div class="card-title" style="margin-bottom:10px;">Результаты проверки <span class="demo-flag" style="background:#f2f4f7;color:var(--text-dim);">эвристика по ключевым словам</span></div>`;

  if(result.missingCategories.length){
    html += `<div class="result-block"><div class="result-head"><b>Категории без совпадений</b><span class="badge badge-amber">${result.missingCategories.length}</span></div>
      <div style="padding:12px 16px;font-size:13px;color:var(--text-dim);">Проверьте, что это не пропуск, а осознанное решение: ${result.missingCategories.join(', ')}.</div></div>`;
  }
  if(result.noQty.length){
    html += `<div class="result-block"><div class="result-head"><b>Позиции без распознанного количества</b><span class="badge badge-gray">${result.noQty.length}</span></div>
      <div style="padding:12px 16px;">${result.noQty.map(it=>`<div class="item-row"><span class="item-name">${it.name}</span><span class="qty">— добавьте "x&lt;число&gt;"</span></div>`).join('')}</div></div>`;
  }
  if(result.duplicates.length){
    html += `<div class="result-block"><div class="result-head"><b>Повторяющиеся наименования</b><span class="badge badge-red">${result.duplicates.length}</span></div>
      <div style="padding:12px 16px;">${result.duplicates.map(d=>`<div class="item-row"><span class="item-name">${d.name}</span><span class="qty">встречается ${d.count} раза</span></div>`).join('')}</div></div>`;
  }
  if(!result.missingCategories.length && !result.noQty.length && !result.duplicates.length){
    html += `<div class="card" style="border-color:var(--green);background:var(--green-bg);color:var(--green);font-weight:600;font-size:13px;">Замечаний не найдено по ${totalItems} позициям (категории, количество, дубликаты).</div>`;
  }
  wrap.innerHTML = html;
}

document.getElementById('runCompletenessBtn').addEventListener('click', ()=>{
  const raw = document.getElementById('completenessBox').value.trim();
  if(!raw){ showToast('Вставьте перечень позиций перед проверкой.'); return; }
  const items = parseList(raw);
  const result = checkCompleteness(items);
  renderCompletenessResults(result, items.length);
});

/* ----------------------- 3. supplier price-list import (Excel/CSV) ----------------------- */
const PRICE_LIST_HEADER_HINTS = {
  name: ['наименован','назван','товар','позици','номенклатур','item','name','description'],
  qty: ['кол-во','количество','шт','qty','quantity','штук'],
  price: ['цена','стоимост','price','сумма','тариф'],
};

function guessColumnIndexes(headerRow){
  const lower = headerRow.map(h=>String(h||'').toLowerCase().trim());
  const findCol = (hints) => {
    for(let i=0;i<lower.length;i++){
      if(hints.some(h=>lower[i].includes(h))) return i;
    }
    return -1;
  };
  return {
    name: findCol(PRICE_LIST_HEADER_HINTS.name),
    qty: findCol(PRICE_LIST_HEADER_HINTS.qty),
    price: findCol(PRICE_LIST_HEADER_HINTS.price),
  };
}

function parseMoneyValue(raw){
  if(raw===null || raw===undefined || raw==='') return null;
  if(typeof raw==='number') return raw;
  const cleaned = String(raw).replace(/[^\d.,-]/g, '').replace(',', '.');
  return cleaned && !isNaN(Number(cleaned)) ? Number(cleaned) : null;
}

// sheetRows: array of arrays of raw cell values (as returned by
// XLSX.utils.sheet_to_json(ws, {header:1})). Returns parsed item rows
// plus the column mapping used (or -1s if no header could be matched).
function rowsFromSheet(sheetRows){
  const nonEmpty = sheetRows.filter(r=>r.some(c=>String(c||'').trim()!==''));
  if(!nonEmpty.length) return { rows: [], cols: null };

  let cols = guessColumnIndexes(nonEmpty[0]);
  let dataStart = 1;
  if(cols.name===-1){
    // No recognizable header — assume column 0 is the name and that
    // every row (including the first) is data, not a header.
    cols = { name: 0, qty: -1, price: -1 };
    dataStart = 0;
  }

  const rows = nonEmpty.slice(dataStart).map(r=>({
    name: String(r[cols.name]||'').trim(),
    qty: cols.qty>=0 ? parseMoneyValue(r[cols.qty]) : null,
    price: cols.price>=0 ? parseMoneyValue(r[cols.price]) : null,
  })).filter(r=>r.name);

  return { rows, cols };
}

function rowsToListText(rows){
  return rows.map(r=> r.qty!=null ? `${r.name} x${r.qty}` : r.name).join('\n');
}

function renderPriceListPreview(rows, cols){
  const wrap = document.getElementById('priceListPreview');
  if(!rows.length){
    wrap.innerHTML = `<div class="card" style="border-color:var(--red);background:var(--red-bg);color:var(--red);font-weight:600;font-size:13px;">Не удалось распознать строки с наименованиями в этом файле.</div>`;
    return;
  }
  const mapNote = (cols && cols.name>=0 && (cols.qty>=0 || cols.price>=0))
    ? `Определены колонки: наименование${cols.qty>=0?', количество':''}${cols.price>=0?', цена':''}.`
    : `Заголовки не распознаны — взята первая колонка как наименование, проверьте результат.`;
  const sampleRows = rows.slice(0,300).map(r=>`<tr>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border);">${r.name}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border);text-align:right;">${r.qty??'—'}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border);text-align:right;">${r.price!=null?r.price.toLocaleString('ru-RU'):'—'}</td>
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

  document.getElementById('pushPriceListA').addEventListener('click', ()=>{
    document.getElementById('compareBoxA').value = rowsToListText(rows);
    showToast('Список вставлен в Комплектацию А.');
  });
  document.getElementById('pushPriceListB').addEventListener('click', ()=>{
    document.getElementById('compareBoxB').value = rowsToListText(rows);
    showToast('Список вставлен в Комплектацию Б.');
  });
  document.getElementById('pushPriceListCompleteness').addEventListener('click', ()=>{
    document.getElementById('completenessBox').value = rowsToListText(rows);
    showToast('Список вставлен в проверку полноты.');
  });
}

document.getElementById('priceListFile').addEventListener('change', (e)=>{
  const file = e.target.files[0];
  if(!file) return;
  if(typeof XLSX === 'undefined'){
    showToast('Не удалось загрузить библиотеку для чтения файла. Проверьте подключение к интернету и обновите страницу.');
    return;
  }
  const ext = file.name.split('.').pop().toLowerCase();
  const reader = new FileReader();
  reader.onload = (ev)=>{
    try{
      const wb = ext==='csv'
        ? XLSX.read(ev.target.result, { type:'binary' })
        : XLSX.read(new Uint8Array(ev.target.result), { type:'array' });
      const ws = wb.Sheets[wb.SheetNames[0]];
      const sheetRows = XLSX.utils.sheet_to_json(ws, { header:1, raw:true, defval:'' });
      const { rows, cols } = rowsFromSheet(sheetRows);
      renderPriceListPreview(rows, cols);
    } catch(err){
      showToast('Не удалось прочитать файл: '+err.message);
    }
  };
  reader.onerror = ()=> showToast('Не удалось прочитать файл.');
  if(ext==='csv') reader.readAsBinaryString(file);
  else reader.readAsArrayBuffer(file);
});
