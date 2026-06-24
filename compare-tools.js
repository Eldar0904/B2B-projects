/* =====================================================================
   COMPARE & COMPLETENESS TOOLS — rule-based, deterministic, no API calls.

   Two independent tools, both working purely on pasted text lists
   ("Name xQty" per line):
     1. Room-kit / spec comparison — diffs two lists (only-in-A,
        only-in-B, quantity mismatches, matched), like a text diff.
     2. Spec completeness checker — keyword-classifies items into
        broad categories (Техника / Мебель / Дидактика) and flags
        categories with zero matches, lines with no recognized
        quantity, and duplicate item names.

   This is intentionally NOT generative/LLM-based — matching is exact
   string normalization + keyword substring checks, so results are
   deterministic and reproducible (and free to run, no API key needed).
   Loaded as a plain classic <script src="compare-tools.js"></script>,
   shares global scope with the main inline script (relies on showToast
   already being defined there).
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

/* ----------------------- 1. comparison / diff ----------------------- */
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

  return { onlyA, onlyB, mismatched, matched };
}

function renderCompareResults(diff){
  const wrap = document.getElementById('compareResultsWrap');
  const qtyLabel = q => q===null ? '—' : q;
  const section = (title, badgeClass, rows) => rows.length ? `
    <div class="result-block">
      <div class="result-head"><b>${title}</b><span class="badge ${badgeClass}">${rows.length}</span></div>
      <div style="padding:12px 16px;">${rows}</div>
    </div>` : '';

  const onlyARows = diff.onlyA.map(it=>`<div class="item-row"><span class="item-name">${it.name}</span><span class="qty">${qtyLabel(it.qty)}</span></div>`).join('');
  const onlyBRows = diff.onlyB.map(it=>`<div class="item-row"><span class="item-name">${it.name}</span><span class="qty">${qtyLabel(it.qty)}</span></div>`).join('');
  const mismatchRows = diff.mismatched.map(it=>`<div class="item-row"><span class="item-name">${it.name}</span><span class="qty">А: ${qtyLabel(it.qtyA)} → Б: ${qtyLabel(it.qtyB)}</span></div>`).join('');

  let html = `<div class="card-title" style="margin-bottom:10px;">Результаты сравнения <span class="demo-flag" style="background:#f2f4f7;color:var(--text-dim);">текстовое сравнение, без внешних сервисов</span></div>`;
  html += section('Только в комплектации А', 'badge-red', onlyARows);
  html += section('Только в комплектации Б', 'badge-green', onlyBRows);
  html += section('Совпадают по наименованию, но разное количество', 'badge-amber', mismatchRows);
  if(!diff.onlyA.length && !diff.onlyB.length && !diff.mismatched.length){
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
