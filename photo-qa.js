/* =====================================================================
   PHOTO QA — Gemini Vision (fitout / delivery check)
   Список позиций + фото → JSON: found | missing | unclear
   Photos are resized before send (big phone JPEGs were hanging the UI).
   ===================================================================== */

const PHOTO_QA_VISION_MODELS = [
  'gemini-2.5-flash-lite',
  'gemini-2.5-flash',
  'gemini-2.0-flash-lite',
  'gemini-2.0-flash',
];
const PHOTO_QA_API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models';
const PHOTO_QA_MAX_ITEMS = 40;
const PHOTO_QA_MAX_PHOTOS = 4;
const PHOTO_QA_RATE_WAIT_MS = 45000;
const PHOTO_QA_FETCH_TIMEOUT_MS = 45000;
const PHOTO_QA_API_TIMEOUT_MS = 90000;
const PHOTO_QA_MAX_EDGE = 1280;
const PHOTO_QA_JPEG_QUALITY = 0.72;

let photoQaSelectedIds = new Set();
let photoQaCheckBusy = false;
let photoQaCheckAbort = null;

function photoQaGeminiKey(){
  return (typeof window !== 'undefined' && window.GEMINI_API_KEY)
    || (typeof GEMINI_API_KEY !== 'undefined' ? GEMINI_API_KEY : '')
    || '';
}
function photoQaGeminiReady(){
  const key = photoQaGeminiKey();
  return typeof key === 'string' && key.length > 10 && !key.includes('YOUR_');
}
function photoQaSleep(ms){ return new Promise(r=>setTimeout(r, ms)); }
function photoQaIsRateLimit(msg){
  return /429|quota|rate limit|resource exhausted|too many requests|exceeded your current quota/i.test(String(msg || ''));
}
function photoQaQuotaZero(msg){
  return /limit:\s*0\b/i.test(String(msg || ''));
}
function photoQaRetrySeconds(msg){
  const m = String(msg || '').match(/retry in\s*([\d.]+)\s*s/i);
  if(!m) return null;
  const sec = Math.ceil(Number(m[1]));
  return Number.isFinite(sec) && sec > 0 ? Math.min(sec, 120) : null;
}
function formatPhotoQaGeminiError(msg){
  const raw = String(msg || '');
  if(photoQaQuotaZero(raw) || (/free_tier/i.test(raw) && /quota|exceeded/i.test(raw))){
    return 'Квота Gemini Free = 0 для этого ключа/проекта. Создайте новый API key на aistudio.google.com/apikey (обычно начинается с AIza…) или включите billing в Google AI Studio. Старый ключ AQ.… часто не даёт free tier.';
  }
  if(photoQaIsRateLimit(raw)){
    const sec = photoQaRetrySeconds(raw);
    return sec
      ? `Лимит запросов Gemini. Подождите ~${sec} с и нажмите проверку снова.`
      : 'Лимит запросов Gemini. Подождите минуту и попробуйте снова (1 фото, короткий список).';
  }
  if(/API key|invalid|permission|401|403/i.test(raw)){
    return 'Ключ Gemini отклонён. Создайте ключ AIza… на aistudio.google.com/apikey и вставьте в config.local.js';
  }
  return raw;
}

function parsePhotoQaItems(raw){
  return String(raw || '')
    .split(/\r?\n/)
    .map(l=>l.replace(/^[\s\-*•\d.)]+/, '').trim())
    .filter(Boolean)
    .slice(0, PHOTO_QA_MAX_ITEMS);
}

function extractPhotoQaJsonArray(text){
  const trimmed = String(text || '').trim();
  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const raw = fenced ? fenced[1].trim() : trimmed;
  const start = raw.indexOf('[');
  const end = raw.lastIndexOf(']');
  if(start === -1 || end === -1) throw new Error('Нет JSON-массива в ответе ИИ');
  return JSON.parse(raw.slice(start, end + 1));
}

function setPhotoQaProgress(pct, label){
  const track = document.getElementById('photoQaProgressWrap');
  const fill = document.getElementById('photoQaProgressFill');
  const status = document.getElementById('photoQaCheckStatus');
  const pctEl = document.getElementById('photoQaProgressPct');
  const p = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
  if(track) track.style.display = (label || p > 0) ? 'block' : 'none';
  if(fill) fill.style.width = p + '%';
  if(pctEl) pctEl.textContent = p + '%';
  if(status) status.textContent = label || '';
}

function clearPhotoQaProgress(){
  setPhotoQaProgress(0, '');
  const track = document.getElementById('photoQaProgressWrap');
  if(track) track.style.display = 'none';
}

function blobToBase64(blob){
  return new Promise((resolve, reject)=>{
    const reader = new FileReader();
    reader.onload = ()=>{
      const dataUrl = String(reader.result || '');
      const base64 = dataUrl.includes(',') ? dataUrl.split(',')[1] : dataUrl;
      resolve(base64);
    };
    reader.onerror = ()=>reject(new Error('Не удалось прочитать изображение'));
    reader.readAsDataURL(blob);
  });
}

function loadImageFromBlob(blob){
  return new Promise((resolve, reject)=>{
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = ()=>{ URL.revokeObjectURL(url); resolve(img); };
    img.onerror = ()=>{ URL.revokeObjectURL(url); reject(new Error('Повреждённое изображение')); };
    img.src = url;
  });
}

async function resizeImageBlob(blob, maxEdge, quality){
  const img = await loadImageFromBlob(blob);
  const w = img.naturalWidth || img.width;
  const h = img.naturalHeight || img.height;
  const scale = Math.min(1, maxEdge / Math.max(w, h));
  const tw = Math.max(1, Math.round(w * scale));
  const th = Math.max(1, Math.round(h * scale));
  const canvas = document.createElement('canvas');
  canvas.width = tw;
  canvas.height = th;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0, tw, th);
  const outBlob = await new Promise(resolve=>{
    canvas.toBlob(b=>resolve(b), 'image/jpeg', quality);
  });
  return outBlob || blob;
}

async function fetchWithTimeout(url, ms, signal){
  const ctrl = new AbortController();
  const onAbort = ()=>ctrl.abort();
  if(signal){
    if(signal.aborted) ctrl.abort();
    else signal.addEventListener('abort', onAbort, { once: true });
  }
  const timer = setTimeout(()=>ctrl.abort(), ms);
  try{
    return await fetch(url, { mode: 'cors', signal: ctrl.signal });
  }finally{
    clearTimeout(timer);
    if(signal) signal.removeEventListener('abort', onAbort);
  }
}

async function fetchPhotoAsInlineData(url, signal){
  const res = await fetchWithTimeout(url, PHOTO_QA_FETCH_TIMEOUT_MS, signal);
  if(!res.ok) throw new Error('Не удалось скачать фото ('+res.status+'). Проверьте публичный URL Supabase.');
  let blob = await res.blob();
  try{
    blob = await resizeImageBlob(blob, PHOTO_QA_MAX_EDGE, PHOTO_QA_JPEG_QUALITY);
  }catch(e){
    /* keep original if canvas fails */
  }
  const data = await blobToBase64(blob);
  return { mime_type: 'image/jpeg', data };
}

async function geminiVisionGenerate(parts, onStatus, signal){
  let lastErr;
  for(const model of PHOTO_QA_VISION_MODELS){
    if(signal && signal.aborted) throw new Error('Отменено');
    for(let attempt = 0; attempt < 2; attempt++){
      try{
        if(onStatus) onStatus(`Отправка в Gemini (${model})…`);
        const ctrl = new AbortController();
        const onAbort = ()=>ctrl.abort();
        if(signal){
          if(signal.aborted) ctrl.abort();
          else signal.addEventListener('abort', onAbort, { once: true });
        }
        const timer = setTimeout(()=>ctrl.abort(), PHOTO_QA_API_TIMEOUT_MS);
        let res;
        try{
          res = await fetch(
            `${PHOTO_QA_API_BASE}/${model}:generateContent?key=${encodeURIComponent(photoQaGeminiKey())}`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                contents: [{ parts }],
                generationConfig: { temperature: 0.1, maxOutputTokens: 4096 },
              }),
              signal: ctrl.signal,
            }
          );
        }finally{
          clearTimeout(timer);
          if(signal) signal.removeEventListener('abort', onAbort);
        }
        const data = await res.json().catch(()=>({}));
        if(!res.ok){
          const msg = (data.error && data.error.message) || res.statusText;
          if(photoQaIsRateLimit(msg)){
            if(photoQaQuotaZero(msg)) throw new Error(msg);
            const waitSec = photoQaRetrySeconds(msg) || Math.round(PHOTO_QA_RATE_WAIT_MS/1000);
            if(attempt < 1){
              if(onStatus) onStatus(`Лимит API — пауза ${waitSec} с…`);
              await photoQaSleep(waitSec * 1000);
              continue;
            }
            throw new Error(msg);
          }
          if(/not found|404|no longer available|deprecated|unsupported|not supported/i.test(msg)){
            lastErr = new Error(msg);
            break;
          }
          throw new Error(msg);
        }
        const outParts = (data.candidates && data.candidates[0] && data.candidates[0].content
          && data.candidates[0].content.parts) || [];
        return outParts.map(p=>p.text || '').join('\n');
      }catch(err){
        lastErr = err;
        if(err && err.name === 'AbortError'){
          throw new Error('Превышено время ожидания ответа Gemini (90 с). Попробуйте 1 фото и короткий список.');
        }
        if(photoQaIsRateLimit(err.message)){
          if(photoQaQuotaZero(err.message)) throw err;
          const waitSec = photoQaRetrySeconds(err.message) || Math.round(PHOTO_QA_RATE_WAIT_MS/1000);
          if(attempt < 1){
            if(onStatus) onStatus(`Лимит API — пауза ${waitSec} с…`);
            await photoQaSleep(waitSec * 1000);
            continue;
          }
          throw err;
        }
        if(/not found|404|no longer available|deprecated|unsupported/i.test(String(err.message))) break;
        throw err;
      }
    }
  }
  throw lastErr || new Error('Gemini Vision недоступен');
}

function normalizePhotoQaResults(items, rows){
  const byItem = new Map();
  (rows || []).forEach(row=>{
    const key = String(row.item || row.name || '').trim().toLowerCase();
    if(key) byItem.set(key, row);
  });
  return items.map((item, idx)=>{
    const row = byItem.get(item.toLowerCase()) || (rows && rows[idx]) || {};
    let status = String(row.status || 'unclear').toLowerCase();
    if(status === 'found' || status === 'найдено' || status === 'present' || status === 'yes') status = 'found';
    else if(status === 'missing' || status === 'нет' || status === 'absent' || status === 'no' || status === 'not_found') status = 'missing';
    else status = 'unclear';
    let confidence = Number(row.confidence);
    if(!Number.isFinite(confidence)) confidence = status === 'unclear' ? 0.4 : 0.6;
    confidence = Math.min(1, Math.max(0, confidence));
    return {
      item,
      status,
      confidence,
      note: String(row.note || row.comment || row.reason || '').slice(0, 240),
    };
  });
}

function summarizePhotoQaResults(results){
  return {
    found: results.filter(r=>r.status==='found').length,
    missing: results.filter(r=>r.status==='missing').length,
    unclear: results.filter(r=>r.status==='unclear').length,
    total: results.length,
  };
}

async function runPhotoQaVisionCheck(items, photos, onProgress, signal){
  const list = items.map((it, i)=>`${i + 1}. ${it}`).join('\n');
  const prompt = `Ты контролируешь комплектацию / поставку для B2B Fitout (школы, кабинеты, залы).

На фото — помещение или поставленное оборудование.
Ниже список позиций. Для КАЖДОЙ позиции определи, видна ли она на фото (хотя бы предположительно).

Правила:
- status: "found" | "missing" | "unclear"
- found — объект явно или почти наверняка виден
- missing — на этих фото его нет
- unclear — плохо видно / обрезано / не уверен
- Не выдумывай объекты, которых нет на изображении
- confidence: число от 0 до 1
- note: кратко по-русски (что видно или почему неясно)

Позиции:
${list}

Верни ТОЛЬКО JSON-массив (без markdown), поле item — точная строка позиции:
[{"item":"...","status":"found","confidence":0.85,"note":"..."}]`;

  const parts = [{ text: prompt }];
  const n = photos.length || 1;
  for(let i = 0; i < photos.length; i++){
    if(signal && signal.aborted) throw new Error('Отменено');
    const pct = 8 + Math.round((i / n) * 52);
    if(onProgress) onProgress(pct, `Сжимаю фото ${i + 1}/${photos.length}…`);
    const inline = await fetchPhotoAsInlineData(photos[i].url, signal);
    parts.push({ inline_data: inline });
    if(onProgress) onProgress(8 + Math.round(((i + 1) / n) * 52), `Фото ${i + 1}/${photos.length} готово`);
  }
  if(onProgress) onProgress(65, 'Отправка в Gemini…');
  const text = await geminiVisionGenerate(parts, (label)=>{
    if(onProgress) onProgress(78, label);
  }, signal);
  if(onProgress) onProgress(90, 'Разбор ответа…');
  const parsed = extractPhotoQaJsonArray(text);
  const results = normalizePhotoQaResults(items, parsed);
  return {
    results,
    summary: summarizePhotoQaResults(results),
    rawPreview: String(text || '').slice(0, 200),
  };
}

function photoQaStatusBadgeHtml(status){
  if(status === 'found') return '<span class="badge badge-green">Найдено</span>';
  if(status === 'missing') return '<span class="badge badge-red">Нет</span>';
  return '<span class="badge badge-amber">Неясно</span>';
}

function renderPhotoQaResults(payload){
  const wrap = document.getElementById('photoQaResultsWrap');
  if(!wrap) return;
  if(!payload || !payload.results){
    wrap.style.display = 'none';
    wrap.innerHTML = '';
    return;
  }
  const s = payload.summary || summarizePhotoQaResults(payload.results);
  wrap.style.display = 'block';
  wrap.innerHTML = `
    <div class="card-head" style="margin-bottom:10px;">
      <div class="card-title">Результат проверки</div>
      <div style="font-size:12px;color:var(--text-dim);">
        найдено ${s.found} · нет ${s.missing} · неясно ${s.unclear}
        ${payload.photoNames ? ` · фото: ${payload.photoNames}` : ''}
      </div>
    </div>
    <table class="doc-table photoqa-results-table">
      <thead><tr><th>Позиция</th><th>Статус</th><th>Увер.</th><th>Комментарий</th></tr></thead>
      <tbody>
        ${payload.results.map(r=>`
          <tr>
            <td>${escapeHtml(r.item)}</td>
            <td>${photoQaStatusBadgeHtml(r.status)}</td>
            <td>${Math.round((r.confidence||0)*100)}%</td>
            <td style="color:var(--text-dim);font-size:12.5px;">${escapeHtml(r.note||'—')}</td>
          </tr>`).join('')}
      </tbody>
    </table>
    <p style="font-size:11.5px;color:var(--text-faint);margin:10px 0 0;">
      Оценка ИИ предварительная — сверяйте на объекте. Результаты сохранены у выбранных фото.
    </p>`;
}

function updatePhotoQaCheckButton(){
  const btn = document.getElementById('photoQaCheckBtn');
  const cancelBtn = document.getElementById('photoQaCancelBtn');
  if(!btn) return;
  const hasPhotos = currentProjectId
    && photoChecks.some(p=>p.projectId===currentProjectId && p.url);
  btn.disabled = photoQaCheckBusy || !hasPhotos;
  btn.title = !hasPhotos
    ? 'Сначала загрузите фото'
    : (photoQaGeminiReady() ? 'Проверить выбранные фото по списку позиций' : 'Нужен GEMINI_API_KEY в config.local.js');
  btn.textContent = photoQaCheckBusy ? 'Проверка…' : 'Проверить с ИИ';
  if(cancelBtn) cancelBtn.style.display = photoQaCheckBusy ? '' : 'none';
}

function getSelectedPhotoQaPhotos(){
  const projectPhotos = photoChecks.filter(p=>p.projectId===currentProjectId && p.url);
  let selected = projectPhotos.filter(p=>photoQaSelectedIds.has(p.id));
  if(!selected.length) selected = projectPhotos.slice(0, PHOTO_QA_MAX_PHOTOS);
  return selected.slice(0, PHOTO_QA_MAX_PHOTOS);
}

async function handlePhotoQaCheck(){
  if(photoQaCheckBusy) return;
  if(!currentProjectId){ showToast('Сначала откройте проект.'); return; }
  if(!photoQaGeminiReady()){
    showToast('Укажите GEMINI_API_KEY в config.local.js (aistudio.google.com/apikey).');
    return;
  }
  const items = parsePhotoQaItems(document.getElementById('photoQaItemsBox').value);
  if(!items.length){
    showToast('Вставьте список позиций (одна строка — одна позиция).');
    document.getElementById('photoQaItemsBox').focus();
    return;
  }
  const photos = getSelectedPhotoQaPhotos();
  if(!photos.length){
    showToast('Нет фото для проверки — загрузите снимки.');
    return;
  }

  photoQaCheckBusy = true;
  photoQaCheckAbort = new AbortController();
  updatePhotoQaCheckButton();
  setPhotoQaProgress(3, `Старт · ${photos.length} фото · ${items.length} позиций`);

  const ids = photos.map(p=>p.id);
  try{
    await Promise.all(ids.map(id=>
      db.collection('photoChecks').doc(id).update({ status: 'checking' })
    ));
  }catch(e){ /* soft */ }

  try{
    setPhotoQaProgress(6, 'Готовлю изображения…');
    const { results, summary } = await runPhotoQaVisionCheck(
      items,
      photos,
      (pct, label)=>setPhotoQaProgress(pct, label),
      photoQaCheckAbort.signal
    );
    setPhotoQaProgress(94, 'Сохраняю результат…');
    const checkedAt = new Date().toISOString();
    const aiResult = {
      checkedAt,
      itemCount: items.length,
      photoIds: ids,
      results,
      summary,
      engine: 'gemini-vision',
    };
    await Promise.all(ids.map(id=>
      db.collection('photoChecks').doc(id).update({
        status: 'checked',
        aiResult,
        lastCheckedAt: checkedAt,
      })
    ));
    setPhotoQaProgress(100, 'Готово');
    renderPhotoQaResults({
      results,
      summary,
      photoNames: photos.map(p=>p.name).join(', '),
    });
    logActivity(
      `Было проверено ${photos.length} фото (${summary.found} найдено / ${summary.missing} нет / ${summary.unclear} неясно)`
    );
    showToast(`Готово: найдено ${summary.found}, нет ${summary.missing}, неясно ${summary.unclear}`);
    setTimeout(clearPhotoQaProgress, 1200);
  }catch(err){
    await Promise.all(ids.map(id=>
      db.collection('photoChecks').doc(id).update({ status: 'uploaded' }).catch(()=>{})
    ));
    const msg = (err && err.message) || String(err);
    clearPhotoQaProgress();
    if(/отменено/i.test(msg)){
      showToast('Проверка остановлена.');
    } else if(/Failed to fetch|CORS|NetworkError/i.test(msg)){
      showToast('Не удалось загрузить фото для ИИ (CORS). Проверьте публичный доступ Supabase Storage.');
    } else {
      showToast(formatPhotoQaGeminiError(msg));
    }
  }finally{
    photoQaCheckBusy = false;
    photoQaCheckAbort = null;
    updatePhotoQaCheckButton();
  }
}

function cancelPhotoQaCheck(){
  if(photoQaCheckAbort) photoQaCheckAbort.abort();
  setPhotoQaProgress(0, 'Отмена…');
}

function wirePhotoQaVisionUi(){
  const btn = document.getElementById('photoQaCheckBtn');
  if(btn && !btn.dataset.visionWired){
    btn.dataset.visionWired = '1';
    btn.addEventListener('click', ()=>{ handlePhotoQaCheck(); });
  }
  const cancelBtn = document.getElementById('photoQaCancelBtn');
  if(cancelBtn && !cancelBtn.dataset.visionWired){
    cancelBtn.dataset.visionWired = '1';
    cancelBtn.addEventListener('click', ()=>{ cancelPhotoQaCheck(); });
  }
  updatePhotoQaCheckButton();
}

document.addEventListener('DOMContentLoaded', wirePhotoQaVisionUi);
wirePhotoQaVisionUi();
