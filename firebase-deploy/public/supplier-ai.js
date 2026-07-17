/* =====================================================================
   AI SUPPLIER SEARCH — Gemini + Google Search (free tier)
   Пакетная обработка больших списков (сотни позиций).
   ===================================================================== */

const sampleItems = [
  'Школьные парты (2-местные) x120',
  'Стулья для класса x240',
  'Интерактивные доски 75" x15',
  'Документ-камеры x15',
  'Мобильные зарядные шкафы x20',
];

const SUPPLIER_SEARCH_REGION = 'Казахстан';
const SUPPLIER_BATCH_SIZE = 10;
const GEMINI_BATCH_DELAY_MS = 5000;
const GEMINI_RATE_LIMIT_WAIT_MS = 65000;
const GEMINI_MODELS = ['gemini-2.0-flash', 'gemini-2.5-flash'];
const GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models';

const supplierDB = {
  'student desks': [
    { name:'EuroSchool Furniture LLC', loc:'Алматы, Казахстан', price:'$68/шт', lead:'3–4 недели', moq:'50 шт', rating:4.6, best:true, src:'demo', url:'' },
    { name:'Kazakhstan Office & School Furnishings', loc:'Астана, Казахстан', price:'$74/шт', lead:'2–3 недели', moq:'20 шт', rating:4.3, src:'demo', url:'' },
  ],
  'classroom chairs': [
    { name:'KZ FurnPro', loc:'Шымкент, Казахстан', price:'$22/шт', lead:'2 недели', moq:'100 шт', rating:4.5, best:true, src:'demo', url:'' },
  ],
  'interactive whiteboards': [
    { name:'BrightBoard EdTech', loc:'Астана, Казахстан', price:'$1 240/шт', lead:'4 недели', moq:'5 шт', rating:4.7, best:true, src:'demo', url:'' },
  ],
  'document cameras': [
    { name:'BrightBoard EdTech', loc:'Астана, Казахстан', price:'$210/шт', lead:'3 недели', moq:'5 шт', rating:4.5, best:true, src:'demo', url:'' },
  ],
  'charging cabinets': [
    { name:'TechStore Cabinets KZ', loc:'Алматы, Казахстан', price:'$340/шт', lead:'2 недели', moq:'5 шт', rating:4.4, best:true, src:'demo', url:'' },
  ],
  'default': [
    { name:'Central Asia Procurement Partners', loc:'Алматы, Казахстан', price:'Цена по запросу', lead:'2–4 недели', moq:'—', rating:4.2, best:true, src:'demo', url:'' },
  ],
};

function matchSupplierKey(itemName){
  const n = itemName.toLowerCase();
  if(n.includes('парт')) return 'student desks';
  if(n.includes('стул')) return 'classroom chairs';
  if(n.includes('доск')) return 'interactive whiteboards';
  if(n.includes('камер')) return 'document cameras';
  if(n.includes('шкаф')) return 'charging cabinets';
  return 'default';
}

function isGeminiConfigured(){
  return typeof GEMINI_API_KEY === 'string'
    && GEMINI_API_KEY.length > 10
    && !GEMINI_API_KEY.includes('YOUR_');
}

function sleep(ms){ return new Promise(r=>setTimeout(r, ms)); }

function chunkArray(arr, size){
  const out = [];
  for(let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

function extractJsonArray(text){
  const trimmed = String(text || '').trim();
  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const raw = fenced ? fenced[1].trim() : trimmed;
  const start = raw.indexOf('[');
  const end = raw.lastIndexOf(']');
  if(start === -1 || end === -1) return [];
  return JSON.parse(raw.slice(start, end + 1));
}

function normalizeSupplier(row){
  const rating = Number(row.rating);
  return {
    name: String(row.name || '—').slice(0, 120),
    loc: String(row.loc || '—').slice(0, 120),
    price: String(row.price || 'Цена по запросу').slice(0, 80),
    lead: String(row.lead || '—').slice(0, 80),
    moq: String(row.moq || '—').slice(0, 80),
    rating: Number.isFinite(rating) ? Math.min(5, Math.max(1, rating)) : 4.0,
    best: Boolean(row.best),
    src: String(row.src || 'web').slice(0, 80),
    url: String(row.url || '').slice(0, 500),
  };
}

function ensureOneBest(suppliers){
  if(!suppliers.length) return suppliers;
  if(!suppliers.some(s=>s.best)) suppliers[0].best = true;
  return suppliers;
}

function isRateLimitError(msg){
  return /429|quota|rate limit|resource exhausted|too many requests/i.test(String(msg || ''));
}

async function geminiGenerateWithSearch(prompt){
  let lastErr;
  for(const model of GEMINI_MODELS){
    for(let attempt = 0; attempt < 3; attempt++){
      try{
        const res = await fetch(`${GEMINI_API_BASE}/${model}:generateContent?key=${encodeURIComponent(GEMINI_API_KEY)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            contents: [{ parts: [{ text: prompt }] }],
            tools: [{ google_search: {} }],
            generationConfig: { temperature: 0.2, maxOutputTokens: 4096 },
          }),
        });
        const data = await res.json().catch(()=>({}));
        if(!res.ok){
          const msg = (data.error && data.error.message) || res.statusText;
          if(isRateLimitError(msg) && attempt < 2){
            await sleep(GEMINI_RATE_LIMIT_WAIT_MS);
            continue;
          }
          throw new Error(msg);
        }
        const parts = (data.candidates && data.candidates[0] && data.candidates[0].content && data.candidates[0].content.parts) || [];
        const text = parts.map(p=>p.text || '').join('\n');
        const groundingChunks = data.candidates && data.candidates[0]
          && data.candidates[0].groundingMetadata
          && data.candidates[0].groundingMetadata.groundingChunks;
        return { text, groundingChunks: groundingChunks || [] };
      }catch(err){
        lastErr = err;
        if(isRateLimitError(err.message) && attempt < 2){
          await sleep(GEMINI_RATE_LIMIT_WAIT_MS);
          continue;
        }
        if(/not found|404|model/i.test(String(err.message))) break;
        throw err;
      }
    }
  }
  throw lastErr || new Error('Gemini недоступен');
}

function mapBatchResults(batchItems, parsedRows){
  const byItem = new Map();
  (parsedRows || []).forEach(row=>{
    const key = String(row.item || row.title || row.name || '').trim();
    if(key) byItem.set(key.toLowerCase(), row);
  });
  return batchItems.map((item, idx)=>{
    const row = byItem.get(item.toLowerCase())
      || (parsedRows && parsedRows[idx])
      || null;
    let suppliers = [];
    if(row){
      if(Array.isArray(row.suppliers)) suppliers = row.suppliers;
      else if(row.name) suppliers = [row];
    }
    suppliers = ensureOneBest(suppliers.slice(0, 2).map(normalizeSupplier));
    return { item, suppliers };
  });
}

async function geminiSearchSuppliersBatch(batchItems, region){
  const list = batchItems.map((it, i)=>`${i + 1}. ${it}`).join('\n');
  const prompt = `Ты помощник закупщика B2B Fitout (оснащение школ и кабинетов).

Для КАЖДОЙ позиции ниже найди в интернете до 2 реальных поставщиков или дистрибьюторов.
Регион: ${region}. Приоритет — Казахстан и сайты .kz.
Используй только данные из веб-поиска. Не выдумывай компании.
Если цены нет — «Цена по запросу».

Позиции:
${list}

Верни ТОЛЬКО JSON-массив (без markdown), по одной записи на каждую позицию, поле item — точная строка позиции:
[{"item":"...","suppliers":[{"name":"...","loc":"...","price":"...","lead":"...","moq":"...","rating":4.2,"best":true,"src":"domain.kz","url":"https://..."}]}]

Если для позиции ничего не нашёл — suppliers: [].`;

  const { text } = await geminiGenerateWithSearch(prompt);
  let parsed;
  try{
    parsed = extractJsonArray(text);
  }catch(e){
    console.warn('Gemini batch JSON parse failed:', text.slice(0, 500));
    return batchItems.map(item=>({ item, suppliers: [], error: 'Не удалось разобрать ответ ИИ' }));
  }
  return mapBatchResults(batchItems, parsed);
}

async function searchSuppliersWithGemini(allItems, region, callbacks){
  const { onProgress, onBatchResult } = callbacks || {};
  const results = [];
  const batches = chunkArray(allItems, SUPPLIER_BATCH_SIZE);
  const totalBatches = batches.length;

  for(let b = 0; b < batches.length; b++){
    if(supplierSearchCancel) break;
    const batch = batches[b];
    const itemsDone = Math.min(b * SUPPLIER_BATCH_SIZE, allItems.length);
    if(onProgress){
      onProgress({
        batch: b + 1,
        totalBatches,
        itemsDone,
        totalItems: allItems.length,
        batchItems: batch,
      });
    }
    try{
      const batchResults = await geminiSearchSuppliersBatch(batch, region);
      batchResults.forEach(r=>{
        results.push(r);
        if(onBatchResult) onBatchResult(r, results.length, allItems.length);
      });
    }catch(err){
      batch.forEach(item=>{
        const r = { item, suppliers: [], error: (err && err.message) || String(err) };
        results.push(r);
        if(onBatchResult) onBatchResult(r, results.length, allItems.length);
      });
    }
    if(b < batches.length - 1 && !supplierSearchCancel) await sleep(GEMINI_BATCH_DELAY_MS);
  }

  const etaMin = Math.ceil((totalBatches * (GEMINI_BATCH_DELAY_MS + 8000)) / 60000);
  return {
    mode: 'live',
    region,
    results,
    engine: 'gemini',
    cancelled: supplierSearchCancel,
    warning: allItems.length > SUPPLIER_BATCH_SIZE
      ? `Большой список: ${allItems.length} поз. · ${totalBatches} пакетов по ${SUPPLIER_BATCH_SIZE} · ~${etaMin} мин. Можно остановить в любой момент.`
      : null,
  };
}

function supplierCardHtml(s, idx, si){
  const srcBtn = s.url
    ? `<a class="btn btn-ghost btn-sm" href="${escapeSupplierHtml(s.url)}" target="_blank" rel="noopener">Источник</a>`
    : `<button class="btn btn-ghost btn-sm" type="button" disabled title="Ссылка недоступна">Источник</button>`;
  return `<div class="supplier-card ${s.best?'best':''}">
    ${s.best?'<span class="best-tag">Лучшее совпадение</span>':''}
    <div class="supplier-name">${escapeSupplierHtml(s.name)}</div>
    <div class="supplier-loc">${escapeSupplierHtml(s.loc)}</div>
    <div class="supplier-price">${escapeSupplierHtml(s.price)}</div>
    <div class="supplier-meta">
      <span>Срок: ${escapeSupplierHtml(s.lead)}</span>
      <span>MOQ: ${escapeSupplierHtml(s.moq)}</span>
    </div>
    <div class="stars">${'★'.repeat(Math.round(s.rating))}${'☆'.repeat(5-Math.round(s.rating))} ${s.rating}</div>
    <div class="supplier-actions">
      ${srcBtn}
      <button class="btn btn-primary btn-sm" data-add-shortlist="${idx}-${si}">+ В шортлист</button>
    </div>
  </div>`;
}

function resultBlockHtml(block, idx){
  const suppliers = block.suppliers || [];
  const errNote = block.error ? `<span class="badge badge-gray" style="margin-left:8px;">${escapeSupplierHtml(block.error)}</span>` : '';
  return `<div class="result-block" data-result-idx="${idx}">
    <div class="result-head"><b>${escapeSupplierHtml(block.item)}</b><span class="badge badge-blue">найдено: ${suppliers.length}</span>${errNote}</div>
    ${suppliers.length
      ? `<div class="supplier-grid">${suppliers.map((s,si)=>supplierCardHtml(s, idx, si)).join('')}</div>`
      : `<p style="font-size:13px;color:var(--text-dim);margin:8px 0 0;padding:0 14px 14px;">Поставщики не найдены.</p>`}
  </div>`;
}

function escapeSupplierHtml(str){
  return String(str||'')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

function wireShortlistButtons(){
  /* delegated once below */
}

document.getElementById('resultsWrap').addEventListener('click', e=>{
  const btn = e.target.closest('[data-add-shortlist]');
  if(!btn || btn.disabled) return;
  shortlist.push(btn.dataset.addShortlist);
  document.getElementById('shortlistCount').textContent = shortlist.length;
  document.getElementById('shortlistBar').classList.add('show');
  btn.textContent = 'Добавлено ✓';
  btn.disabled = true;
});

let shortlist = [];
let parsedItems = [];
let supplierSearchRunning = false;
let supplierSearchCancel = false;
let liveSearchResults = [];

const projSelect = document.getElementById('procProjectSelect');
function refreshProjSelect(){
  projSelect.innerHTML = `<option value="">Без проекта (общий поиск)</option>` + projects.map(p=>`<option value="${p.id}">${p.name}</option>`).join('');
}
refreshProjSelect();

document.getElementById('loadSampleBtn').addEventListener('click', ()=>{
  document.getElementById('importBox').value = sampleItems.join('\n');
});

document.getElementById('parseItemsBtn').addEventListener('click', ()=>{
  const raw = document.getElementById('importBox').value.trim();
  if(!raw){ showToast('Сначала вставьте или загрузите список позиций.'); return; }
  parsedItems = raw.split('\n').map(l=>l.trim()).filter(Boolean);
  const rows = parsedItems.map((it)=>{
    const m = it.match(/x(\d+)\s*$/i);
    const qty = m ? `x${m[1]}` : '—';
    return `<div class="item-row"><span class="item-name">${escapeSupplierHtml(it)}</span><span class="qty">${qty}</span></div>`;
  }).join('');
  const batches = Math.ceil(parsedItems.length / SUPPLIER_BATCH_SIZE);
  document.getElementById('itemsList').innerHTML =
    `<p style="font-size:12.5px;color:var(--text-dim);margin:0 0 8px;"><b>${parsedItems.length}</b> позиций · ~${batches} пакетов по ${SUPPLIER_BATCH_SIZE} · все будут обработаны</p>` +
    `<div style="max-height:240px;overflow-y:auto;border:1px solid var(--border);border-radius:8px;padding:4px 8px;">${rows}</div>`;
  document.getElementById('itemsCard').style.display='block';
  document.getElementById('agentCard').style.display='none';
  document.getElementById('resultsWrap').innerHTML='';
});

function runAgentSteps(steps, onDone){
  const agentCard = document.getElementById('agentCard');
  const stepsEl = document.getElementById('agentSteps');
  stepsEl.innerHTML = steps.map((s,i)=>`<div class="agent-step" id="step-${i}"><div class="dot"></div><span id="step-text-${i}">${s}</span></div>`).join('');
  agentCard.style.display='block';
  document.getElementById('searchProgressWrap').style.display='';
  document.getElementById('resultsWrap').innerHTML='';

  let i = 0;
  function advance(){
    if(i > 0) document.getElementById('step-'+(i-1)).classList.replace('active','done');
    if(i < steps.length){
      document.getElementById('step-'+i).classList.add('active');
      i++;
      setTimeout(advance, i === steps.length ? 0 : 400);
    } else if(onDone) onDone();
  }
  advance();
}

function updateAgentStep(idx, text){
  const el = document.getElementById('step-text-'+idx);
  if(el) el.textContent = text;
}

function updateSearchProgress(done, total, label){
  const pct = total ? Math.round((done / total) * 100) : 0;
  document.getElementById('searchProgressFill').style.width = pct + '%';
  document.getElementById('searchProgressLabel').textContent = label || `${done} / ${total} позиций (${pct}%)`;
}

function finishAgentSteps(stepCount){
  for(let j = 0; j < stepCount; j++){
    const el = document.getElementById('step-'+j);
    if(el) el.classList.remove('active');
    if(el) el.classList.add('done');
  }
}

function initLiveResultsHeader(total, warning){
  liveSearchResults = [];
  const flag = '<span class="demo-flag" style="background:#dcfce7;color:#15803d;">Gemini + Google Search</span>';
  document.getElementById('resultsWrap').innerHTML =
    `<div class="card-title" style="margin-bottom:10px;">Результаты ${flag} · <span id="resultsProgressCount">0 / ${total}</span></div>` +
    (warning ? `<p id="searchWarningText" style="font-size:12.5px;color:var(--text-dim);margin:0 0 14px;">${escapeSupplierHtml(warning)}</p>` : '') +
    `<div id="resultsBlocks"></div>`;
}

function appendLiveResultBlock(block){
  liveSearchResults.push(block);
  const idx = liveSearchResults.length - 1;
  const blocks = document.getElementById('resultsBlocks');
  if(blocks) blocks.insertAdjacentHTML('beforeend', resultBlockHtml(block, idx));
  const countEl = document.getElementById('resultsProgressCount');
  if(countEl) countEl.textContent = `${liveSearchResults.length} / ${parsedItems.length}`;
}

function renderDemoResults(){
  supplierRunsCount++;
  renderKPIs();
  const wrap = document.getElementById('resultsWrap');
  wrap.innerHTML = `<div class="card-title" style="margin-bottom:10px;">Результаты <span class="demo-flag">Демо — укажите GEMINI_API_KEY</span></div>` +
    parsedItems.slice(0, 20).map((it,idx)=>{
      const key = matchSupplierKey(it);
      const suppliers = supplierDB[key];
      return resultBlockHtml({ item: it, suppliers }, idx);
    }).join('') +
    (parsedItems.length > 20 ? `<p style="font-size:12.5px;color:var(--text-dim);">… и ещё ${parsedItems.length - 20} позиций (демо только для первых 20)</p>` : '');
}

document.getElementById('cancelSearchBtn').addEventListener('click', ()=>{
  if(!supplierSearchRunning) return;
  supplierSearchCancel = true;
  showToast('Остановка после текущего пакета…');
  document.getElementById('cancelSearchBtn').disabled = true;
});

document.getElementById('runAgentBtn').addEventListener('click', ()=>{
  if(supplierSearchRunning) return;
  if(!parsedItems.length){ showToast('Сначала разберите список позиций.'); return; }
  if(typeof auth !== 'undefined' && !auth.currentUser){
    showToast('Войдите в аккаунт для поиска поставщиков.');
    return;
  }
  if(!isGeminiConfigured()){
    showToast('Укажите GEMINI_API_KEY (aistudio.google.com/apikey).');
    renderDemoResults();
    return;
  }

  const items = parsedItems.slice();
  const batches = Math.ceil(items.length / SUPPLIER_BATCH_SIZE);
  const etaMin = Math.ceil((batches * (GEMINI_BATCH_DELAY_MS + 8000)) / 60000);
  if(items.length > 30){
    if(!confirm(`Обработать все ${items.length} позиций?\n~${batches} пакетов, ориентировочно ${etaMin} мин.\nМожно остановить в процессе.`)) return;
  }

  supplierSearchCancel = false;
  supplierSearchRunning = true;
  const runBtn = document.getElementById('runAgentBtn');
  const cancelBtn = document.getElementById('cancelSearchBtn');
  runBtn.disabled = true;
  cancelBtn.disabled = false;

  const steps = [
    `Список: ${items.length} позиций, ${batches} пакетов…`,
    `Gemini + Google Search (${SUPPLIER_SEARCH_REGION})…`,
    'Извлечение цен и сроков…',
    'Сбор результатов…',
  ];

  const warning = items.length > SUPPLIER_BATCH_SIZE
    ? `${items.length} поз. · ${batches} пакетов · ~${etaMin} мин · free tier ~15 запросов/мин`
    : null;
  initLiveResultsHeader(items.length, warning);

  runAgentSteps(steps, ()=>{
    searchSuppliersWithGemini(items, SUPPLIER_SEARCH_REGION, {
      onProgress: ({ batch, totalBatches, itemsDone, totalItems, batchItems })=>{
        updateAgentStep(1, `Пакет ${batch}/${totalBatches} · позиции ${itemsDone + 1}–${Math.min(itemsDone + batchItems.length, totalItems)}`);
        updateSearchProgress(itemsDone, totalItems, `${itemsDone} / ${totalItems} · пакет ${batch}/${totalBatches}`);
      },
      onBatchResult: (block, done, total)=>{
        appendLiveResultBlock(block);
        updateSearchProgress(done, total);
      },
    })
      .then(data=>{
        finishAgentSteps(steps.length);
        updateSearchProgress(data.results.length, items.length, data.cancelled ? `Остановлено: ${data.results.length} / ${items.length}` : `Готово: ${data.results.length} позиций`);
        supplierRunsCount++;
        renderKPIs();
        if(data.cancelled) showToast(`Остановлено. Обработано ${data.results.length} из ${items.length} позиций.`);
      })
      .catch(err=>{
        console.error('Gemini search failed:', err);
        finishAgentSteps(steps.length);
        showToast('Ошибка Gemini: '+(err.message || err));
      })
      .finally(()=>{
        supplierSearchRunning = false;
        supplierSearchCancel = false;
        runBtn.disabled = false;
        cancelBtn.disabled = true;
      });
  });
});

document.getElementById('clearShortlistBtn').addEventListener('click', ()=>{
  shortlist = [];
  document.getElementById('shortlistBar').classList.remove('show');
});
document.getElementById('sendProcurementBtn').addEventListener('click', ()=>{
  showToast(`${shortlist.length} поставщик(ов) отправлено в закупки (демо).`);
});

const IMPORTABLE_DOC_EXT = ['xlsx', 'xls', 'csv', 'txt'];
let selectedSupplierDocId = null;

function isImportableDocName(name){
  const ext = (name || '').split('.').pop().toLowerCase();
  return IMPORTABLE_DOC_EXT.indexOf(ext) !== -1;
}

function renderSupplierDocPickerList(){
  const listEl = document.getElementById('supplierDocList');
  const statusEl = document.getElementById('supplierDocPickStatus');
  const loadBtn = document.getElementById('supplierDocModalLoad');
  if(!listEl) return;
  selectedSupplierDocId = null;
  if(loadBtn) loadBtn.disabled = true;
  if(statusEl) statusEl.textContent = '';

  const projectId = projSelect.value;
  if(!projectId){
    listEl.innerHTML = '<p style="padding:16px;font-size:13px;color:var(--text-dim);margin:0;">Сначала выберите проект в списке выше.</p>';
    return;
  }

  const docs = (typeof documents !== 'undefined' ? documents : [])
    .filter(d => d.projectId === projectId && d.url)
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''));

  if(!docs.length){
    listEl.innerHTML = '<p style="padding:16px;font-size:13px;color:var(--text-dim);margin:0;">В этом проекте пока нет загруженных документов.</p>';
    return;
  }

  listEl.innerHTML = docs.map(d => {
    const ok = isImportableDocName(d.name);
    const ext = (d.name || '').split('.').pop().toUpperCase().slice(0, 3);
    return `<label class="supplier-doc-row${ok ? '' : ' disabled'}">
      <input type="radio" name="supplierDocPick" value="${d.id}" ${ok ? '' : 'disabled'} style="flex-shrink:0;">
      <span style="width:36px;height:36px;border-radius:8px;background:#ede9fe;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0;">${escapeSupplierHtml(ext)}</span>
      <span style="flex:1;min-width:0;">
        <div style="font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeSupplierHtml(d.name)}</div>
        <div style="font-size:11.5px;color:var(--text-dim);">${escapeSupplierHtml(d.category || '—')} · ${ok ? 'можно импортировать' : 'формат не поддерживается (нужен Excel/CSV/TXT)'}</div>
      </span>
    </label>`;
  }).join('');

  listEl.querySelectorAll('input[type=radio]:not([disabled])').forEach(radio => {
    radio.addEventListener('change', () => {
      selectedSupplierDocId = radio.value;
      if(loadBtn) loadBtn.disabled = false;
      listEl.querySelectorAll('.supplier-doc-row').forEach(r => r.classList.remove('selected'));
      const row = radio.closest('.supplier-doc-row');
      if(row) row.classList.add('selected');
    });
  });
}

function arrayBufferToBinaryString(buf){
  const bytes = new Uint8Array(buf);
  let s = '';
  const chunk = 0x8000;
  for(let i = 0; i < bytes.length; i += chunk){
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return s;
}

async function fetchDocContentAsLines(doc){
  const ext = doc.name.split('.').pop().toLowerCase();
  const resp = await fetch(doc.url);
  if(!resp.ok) throw new Error('Не удалось скачать файл (' + resp.status + ')');

  if(ext === 'txt'){
    return (await resp.text()).trim();
  }

  if(typeof XLSX === 'undefined') throw new Error('Библиотека XLSX не загружена. Обновите страницу.');

  const buf = await resp.arrayBuffer();
  const wb = ext === 'csv'
    ? XLSX.read(arrayBufferToBinaryString(buf), { type: 'binary' })
    : XLSX.read(new Uint8Array(buf), { type: 'array' });
  const ws = wb.Sheets[wb.SheetNames[0]];
  const sheetRows = XLSX.utils.sheet_to_json(ws, { header: 1, raw: true, defval: '' });

  if(typeof rowsFromSheet === 'function' && typeof rowsToListText === 'function'){
    const result = rowsFromSheet(sheetRows);
    if(!result.rows.length) throw new Error('Не удалось распознать позиции в файле');
    return rowsToListText(result.rows);
  }

  const lines = sheetRows.map(r => String(r[0] || '').trim()).filter(Boolean);
  if(!lines.length) throw new Error('Файл пуст или не распознан');
  return lines.join('\n');
}

const supplierDocModal = document.getElementById('supplierDocModalOverlay');
document.getElementById('pickProjectDocBtn')?.addEventListener('click', () => {
  renderSupplierDocPickerList();
  supplierDocModal?.classList.add('show');
});
document.getElementById('supplierDocModalCancel')?.addEventListener('click', () => {
  supplierDocModal?.classList.remove('show');
});
supplierDocModal?.addEventListener('click', e => {
  if(e.target === supplierDocModal) supplierDocModal.classList.remove('show');
});
document.getElementById('supplierDocModalLoad')?.addEventListener('click', async () => {
  const doc = (typeof documents !== 'undefined' ? documents : []).find(d => d.id === selectedSupplierDocId);
  if(!doc) return;

  const statusEl = document.getElementById('supplierDocPickStatus');
  const loadBtn = document.getElementById('supplierDocModalLoad');
  if(loadBtn) loadBtn.disabled = true;
  if(statusEl) statusEl.textContent = 'Загрузка «' + doc.name + '»…';

  try {
    const text = await fetchDocContentAsLines(doc);
    document.getElementById('importBox').value = text;
    supplierDocModal?.classList.remove('show');
    const lineCount = text.split('\n').map(l => l.trim()).filter(Boolean).length;
    showToast('Загружено ' + lineCount + ' позиций из «' + doc.name + '»');
  } catch(err){
    if(statusEl) statusEl.textContent = '';
    showToast(err.message || String(err));
    if(loadBtn) loadBtn.disabled = !selectedSupplierDocId;
  }
});

projSelect.addEventListener('change', () => {
  if(supplierDocModal?.classList.contains('show')) renderSupplierDocPickerList();
});
