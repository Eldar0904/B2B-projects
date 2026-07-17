/* =====================================================================
   AI SUPPLIER SEARCH — component script
   Live search via Cloud Function `searchSuppliers` (Google CSE + Claude).
   Falls back to local demo data if the function is unavailable.

   Globals from main script:
     - projects, auth, showToast, renderKPIs, supplierRunsCount
   Requires: firebase-functions-compat.js loaded before this file.
   ===================================================================== */

const sampleItems = [
  'Школьные парты (2-местные) x120',
  'Стулья для класса x240',
  'Интерактивные доски 75" x15',
  'Документ-камеры x15',
  'Мобильные зарядные шкафы x20',
];

const SUPPLIER_SEARCH_REGION = 'Казахстан';
const SUPPLIER_SEARCH_MAX_ITEMS = 8;
const FUNCTIONS_REGION = 'asia-southeast1';

const supplierDB = {
  'student desks': [
    { name:'EuroSchool Furniture LLC', loc:'Алматы, Казахстан', price:'$68/шт', lead:'3–4 недели', moq:'50 шт', rating:4.6, best:true, src:'eu-schoolfurniture.example', url:'' },
    { name:'Kazakhstan Office & School Furnishings', loc:'Астана, Казахстан', price:'$74/шт', lead:'2–3 недели', moq:'20 шт', rating:4.3, src:'koshf.example', url:'' },
    { name:'NordikEdu Supply', loc:'Таллин, Эстония (импорт)', price:'$81/шт', lead:'6 недель', moq:'100 шт', rating:4.4, src:'nordikedu.example', url:'' },
  ],
  'classroom chairs': [
    { name:'KZ FurnPro', loc:'Шымкент, Казахстан', price:'$22/шт', lead:'2 недели', moq:'100 шт', rating:4.5, best:true, src:'kzfurnpro.example', url:'' },
    { name:'EuroSchool Furniture LLC', loc:'Алматы, Казахстан', price:'$26/шт', lead:'3 недели', moq:'50 шт', rating:4.6, src:'eu-schoolfurniture.example', url:'' },
    { name:'Asia Classroom Supply', loc:'Шанхай, Китай (импорт)', price:'$15/шт', lead:'8 недель', moq:'500 шт', rating:4.0, src:'asiaclass.example', url:'' },
  ],
  'interactive whiteboards': [
    { name:'BrightBoard EdTech', loc:'Астана, Казахстан (дистрибьютор)', price:'$1 240/шт', lead:'4 недели', moq:'5 шт', rating:4.7, best:true, src:'brightboard.example', url:'' },
    { name:'SmartClass Technologies', loc:'Стамбул, Турция (импорт)', price:'$1 095/шт', lead:'6 недель', moq:'10 шт', rating:4.4, src:'smartclasstech.example', url:'' },
    { name:'VisionEd Display Co.', loc:'Сеул, Южная Корея (импорт)', price:'$1 380/шт', lead:'5 недель', moq:'5 шт', rating:4.6, src:'visioned.example', url:'' },
  ],
  'document cameras': [
    { name:'BrightBoard EdTech', loc:'Астана, Казахстан (дистрибьютор)', price:'$210/шт', lead:'3 недели', moq:'5 шт', rating:4.5, best:true, src:'brightboard.example', url:'' },
    { name:'ClassCam Supply', loc:'Варшава, Польша (импорт)', price:'$185/шт', lead:'5 недель', moq:'10 шт', rating:4.2, src:'classcam.example', url:'' },
  ],
  'charging cabinets': [
    { name:'TechStore Cabinets KZ', loc:'Алматы, Казахстан', price:'$340/шт', lead:'2 недели', moq:'5 шт', rating:4.4, best:true, src:'techstorekz.example', url:'' },
    { name:'PowerCart EU', loc:'Вильнюс, Литва (импорт)', price:'$295/шт', lead:'6 недель', moq:'10 шт', rating:4.3, src:'powercart.example', url:'' },
  ],
  'default': [
    { name:'Central Asia Procurement Partners', loc:'Алматы, Казахстан', price:'Цена по запросу', lead:'2–4 недели', moq:'Зависит от объёма', rating:4.2, best:true, src:'caprocurement.example', url:'' },
    { name:'Global EduSupply Network', loc:'Несколько регионов', price:'Цена по запросу', lead:'4–6 недель', moq:'Зависит от объёма', rating:4.0, src:'globaledusupply.example', url:'' },
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

function getSearchSuppliersCallable(){
  return firebase.app().functions(FUNCTIONS_REGION).httpsCallable('searchSuppliers');
}

function supplierCardHtml(s, idx, si){
  const srcBtn = s.url
    ? `<a class="btn btn-ghost btn-sm" href="${s.url}" target="_blank" rel="noopener">Источник</a>`
    : `<button class="btn btn-ghost btn-sm" type="button" disabled title="Ссылка недоступна">Источник</button>`;
  return `<div class="supplier-card ${s.best?'best':''}">
    ${s.best?'<span class="best-tag">Лучшее совпадение</span>':''}
    <div class="supplier-name">${escapeSupplierHtml(s.name)}</div>
    <div class="supplier-loc">${escapeSupplierHtml(s.loc)}</div>
    <div class="supplier-price">${escapeSupplierHtml(s.price)}</div>
    <div class="supplier-meta">
      <span>Срок поставки: ${escapeSupplierHtml(s.lead)}</span>
      <span>Мин. партия: ${escapeSupplierHtml(s.moq)}</span>
    </div>
    <div class="stars">${'★'.repeat(Math.round(s.rating))}${'☆'.repeat(5-Math.round(s.rating))} ${s.rating}</div>
    <div class="supplier-actions">
      ${srcBtn}
      <button class="btn btn-primary btn-sm" data-add-shortlist="${idx}-${si}">+ В шортлист</button>
    </div>
  </div>`;
}

function escapeSupplierHtml(str){
  return String(str||'')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

function wireShortlistButtons(wrap){
  wrap.querySelectorAll('[data-add-shortlist]').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      shortlist.push(btn.dataset.addShortlist);
      document.getElementById('shortlistCount').textContent = shortlist.length;
      document.getElementById('shortlistBar').classList.add('show');
      btn.textContent = 'Добавлено ✓';
      btn.disabled = true;
    });
  });
}

let shortlist = [];
let parsedItems = [];
let supplierSearchRunning = false;

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
  document.getElementById('itemsList').innerHTML = parsedItems.map((it)=>{
    const m = it.match(/x(\d+)\s*$/i);
    const qty = m ? `x${m[1]}` : '—';
    return `<div class="item-row"><span class="item-name">${escapeSupplierHtml(it)}</span><span class="qty">${qty}</span></div>`;
  }).join('');
  document.getElementById('itemsCard').style.display='block';
  document.getElementById('agentCard').style.display='none';
  document.getElementById('resultsWrap').innerHTML='';
});

function runAgentSteps(steps, onDone){
  const agentCard = document.getElementById('agentCard');
  const stepsEl = document.getElementById('agentSteps');
  stepsEl.innerHTML = steps.map((s,i)=>`<div class="agent-step" id="step-${i}"><div class="dot"></div>${s}</div>`).join('');
  agentCard.style.display='block';
  document.getElementById('resultsWrap').innerHTML='';

  let i = 0;
  function advance(){
    if(i > 0) document.getElementById('step-'+(i-1)).classList.replace('active','done');
    if(i < steps.length){
      document.getElementById('step-'+i).classList.add('active');
      i++;
      setTimeout(advance, i === steps.length ? 0 : 600);
    } else if(onDone) onDone();
  }
  advance();
}

function finishAgentSteps(stepCount){
  for(let j = 0; j < stepCount; j++){
    const el = document.getElementById('step-'+j);
    if(el) el.classList.remove('active');
    if(el) el.classList.add('done');
  }
}

function renderDemoResults(){
  supplierRunsCount++;
  renderKPIs();
  const wrap = document.getElementById('resultsWrap');
  wrap.innerHTML = `<div class="card-title" style="margin-bottom:10px;">Результаты <span class="demo-flag">Демо-данные — Cloud Function недоступна</span></div>` +
    parsedItems.map((it,idx)=>{
      const key = matchSupplierKey(it);
      const suppliers = supplierDB[key];
      return `<div class="result-block">
        <div class="result-head"><b>${escapeSupplierHtml(it)}</b><span class="badge badge-blue">найдено: ${suppliers.length}</span></div>
        <div class="supplier-grid">${suppliers.map((s,si)=>supplierCardHtml(s, idx, si)).join('')}</div>
      </div>`;
    }).join('');
  wireShortlistButtons(wrap);
}

function renderLiveResults(data){
  supplierRunsCount++;
  renderKPIs();
  const wrap = document.getElementById('resultsWrap');
  const flag = '<span class="demo-flag" style="background:#dcfce7;color:#15803d;">Живой веб-поиск</span>';
  let html = `<div class="card-title" style="margin-bottom:10px;">Результаты ${flag}</div>`;
  if(data.warning){
    html += `<p style="font-size:12.5px;color:var(--text-dim);margin:0 0 14px;">${escapeSupplierHtml(data.warning)}</p>`;
  }
  html += (data.results || []).map((block, idx)=>{
    const suppliers = block.suppliers || [];
    const errNote = block.error ? `<span class="badge badge-gray" style="margin-left:8px;">${escapeSupplierHtml(block.error)}</span>` : '';
    return `<div class="result-block">
      <div class="result-head"><b>${escapeSupplierHtml(block.item)}</b><span class="badge badge-blue">найдено: ${suppliers.length}</span>${errNote}</div>
      ${suppliers.length
        ? `<div class="supplier-grid">${suppliers.map((s,si)=>supplierCardHtml(s, idx, si)).join('')}</div>`
        : `<p style="font-size:13px;color:var(--text-dim);margin:8px 0 0;">Поставщики не найдены — попробуйте уточнить формулировку или проверьте настройки поиска.</p>`}
    </div>`;
  }).join('');
  wrap.innerHTML = html;
  wireShortlistButtons(wrap);
}

document.getElementById('runAgentBtn').addEventListener('click', ()=>{
  if(supplierSearchRunning) return;
  if(!parsedItems.length){ showToast('Сначала разберите список позиций.'); return; }
  if(typeof auth !== 'undefined' && !auth.currentUser){
    showToast('Войдите в аккаунт для живого поиска поставщиков.');
    return;
  }

  const items = parsedItems.slice(0, SUPPLIER_SEARCH_MAX_ITEMS);
  if(parsedItems.length > SUPPLIER_SEARCH_MAX_ITEMS){
    showToast(`Обрабатываем первые ${SUPPLIER_SEARCH_MAX_ITEMS} позиций (лимит за запуск).`);
  }

  const steps = [
    'Разбор импортированного списка позиций…',
    `Поиск в интернете по ${items.length} позиции(ям) (${SUPPLIER_SEARCH_REGION})…`,
    'Анализ результатов и извлечение цен, MOQ, сроков…',
    'Ранжирование поставщиков…',
  ];

  supplierSearchRunning = true;
  const runBtn = document.getElementById('runAgentBtn');
  runBtn.disabled = true;

  runAgentSteps(steps, ()=>{
    const lastStep = steps.length - 1;
    getSearchSuppliersCallable()({ items, region: SUPPLIER_SEARCH_REGION })
      .then(res=>{
        finishAgentSteps(steps.length);
        renderLiveResults(res.data || {});
        if(res.data && res.data.warning) showToast(res.data.warning);
      })
      .catch(err=>{
        console.error('searchSuppliers failed:', err);
        finishAgentSteps(steps.length);
        const msg = (err && err.message) || String(err);
        showToast('Живой поиск недоступен: '+msg+' — показаны демо-данные.');
        renderDemoResults();
      })
      .finally(()=>{
        supplierSearchRunning = false;
        runBtn.disabled = false;
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
