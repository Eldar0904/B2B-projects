/* =====================================================================
   AI SUPPLIER SEARCH — component script
   Extracted from index.html so this module's data, demo logic, and
   event wiring live in one place.

   Loaded as a plain classic <script src="supplier-ai.js"></script> —
   no bundler, no ES modules — so it shares the page's global scope
   with the main inline script. It relies on the following globals
   already being defined by the main script by the time they're used:
     - projects            (array, kept in sync with Firestore)
     - showToast(msg)       (toast helper)
     - renderKPIs()         (dashboard KPI re-render)
     - supplierRunsCount    (let, declared in main script; incremented here)
   ===================================================================== */

const sampleItems = [
  'Школьные парты (2-местные) x120',
  'Стулья для класса x240',
  'Интерактивные доски 75" x15',
  'Документ-камеры x15',
  'Мобильные зарядные шкафы x20',
];

// supplier results keyed by simplified item name — fictitious demo suppliers
const supplierDB = {
  'student desks': [
    { name:'EuroSchool Furniture LLC', loc:'Алматы, Казахстан', price:'$68/шт', lead:'3–4 недели', moq:'50 шт', rating:4.6, best:true, src:'eu-schoolfurniture.example' },
    { name:'Kazakhstan Office & School Furnishings', loc:'Астана, Казахстан', price:'$74/шт', lead:'2–3 недели', moq:'20 шт', rating:4.3, src:'koshf.example' },
    { name:'NordikEdu Supply', loc:'Таллин, Эстония (импорт)', price:'$81/шт', lead:'6 недель', moq:'100 шт', rating:4.4, src:'nordikedu.example' },
  ],
  'classroom chairs': [
    { name:'KZ FurnPro', loc:'Шымкент, Казахстан', price:'$22/шт', lead:'2 недели', moq:'100 шт', rating:4.5, best:true, src:'kzfurnpro.example' },
    { name:'EuroSchool Furniture LLC', loc:'Алматы, Казахстан', price:'$26/шт', lead:'3 недели', moq:'50 шт', rating:4.6, src:'eu-schoolfurniture.example' },
    { name:'Asia Classroom Supply', loc:'Шанхай, Китай (импорт)', price:'$15/шт', lead:'8 недель', moq:'500 шт', rating:4.0, src:'asiaclass.example' },
  ],
  'interactive whiteboards': [
    { name:'BrightBoard EdTech', loc:'Астана, Казахстан (дистрибьютор)', price:'$1 240/шт', lead:'4 недели', moq:'5 шт', rating:4.7, best:true, src:'brightboard.example' },
    { name:'SmartClass Technologies', loc:'Стамбул, Турция (импорт)', price:'$1 095/шт', lead:'6 недель', moq:'10 шт', rating:4.4, src:'smartclasstech.example' },
    { name:'VisionEd Display Co.', loc:'Сеул, Южная Корея (импорт)', price:'$1 380/шт', lead:'5 недель', moq:'5 шт', rating:4.6, src:'visioned.example' },
  ],
  'document cameras': [
    { name:'BrightBoard EdTech', loc:'Астана, Казахстан (дистрибьютор)', price:'$210/шт', lead:'3 недели', moq:'5 шт', rating:4.5, best:true, src:'brightboard.example' },
    { name:'ClassCam Supply', loc:'Варшава, Польша (импорт)', price:'$185/шт', lead:'5 недель', moq:'10 шт', rating:4.2, src:'classcam.example' },
  ],
  'charging cabinets': [
    { name:'TechStore Cabinets KZ', loc:'Алматы, Казахстан', price:'$340/шт', lead:'2 недели', moq:'5 шт', rating:4.4, best:true, src:'techstorekz.example' },
    { name:'PowerCart EU', loc:'Вильнюс, Литва (импорт)', price:'$295/шт', lead:'6 недель', moq:'10 шт', rating:4.3, src:'powercart.example' },
  ],
  'default': [
    { name:'Central Asia Procurement Partners', loc:'Алматы, Казахстан', price:'Цена по запросу', lead:'2–4 недели', moq:'Зависит от объёма', rating:4.2, best:true, src:'caprocurement.example' },
    { name:'Global EduSupply Network', loc:'Несколько регионов', price:'Цена по запросу', lead:'4–6 недель', moq:'Зависит от объёма', rating:4.0, src:'globaledusupply.example' },
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

let shortlist = [];

/* ===================== SUPPLIER AI ===================== */
const projSelect = document.getElementById('procProjectSelect');
function refreshProjSelect(){
  projSelect.innerHTML = `<option value="">Без проекта (общий поиск)</option>` + projects.map(p=>`<option value="${p.id}">${p.name}</option>`).join('');
}
refreshProjSelect();

document.getElementById('loadSampleBtn').addEventListener('click', ()=>{
  document.getElementById('importBox').value = sampleItems.join('\n');
});

let parsedItems = [];
document.getElementById('parseItemsBtn').addEventListener('click', ()=>{
  const raw = document.getElementById('importBox').value.trim();
  if(!raw){ showToast('Сначала вставьте или загрузите список позиций.'); return; }
  parsedItems = raw.split('\n').map(l=>l.trim()).filter(Boolean);
  document.getElementById('itemsList').innerHTML = parsedItems.map((it,i)=>{
    const m = it.match(/x(\d+)\s*$/i);
    const qty = m ? `x${m[1]}` : '—';
    return `<div class="item-row"><span class="item-name">${it}</span><span class="qty">${qty}</span></div>`;
  }).join('');
  document.getElementById('itemsCard').style.display='block';
  document.getElementById('agentCard').style.display='none';
  document.getElementById('resultsWrap').innerHTML='';
});

document.getElementById('runAgentBtn').addEventListener('click', ()=>{
  const agentCard = document.getElementById('agentCard');
  const stepsEl = document.getElementById('agentSteps');
  const steps = [
    'Разбор импортированного списка позиций…',
    `Поиск в интернете поставщиков по ${parsedItems.length} позиции(ям)…`,
    'Сверка цен, MOQ и сроков поставки…',
    'Ранжирование поставщиков по цене, рейтингу и срокам…',
  ];
  stepsEl.innerHTML = steps.map((s,i)=>`<div class="agent-step" id="step-${i}"><div class="dot"></div>${s}</div>`).join('');
  agentCard.style.display='block';
  document.getElementById('resultsWrap').innerHTML='';

  let i=0;
  function advance(){
    if(i>0) document.getElementById('step-'+(i-1)).classList.replace('active','done');
    if(i<steps.length){
      const el = document.getElementById('step-'+i);
      el.classList.add('active');
      i++;
      setTimeout(advance, 700);
    } else {
      document.getElementById('step-'+(steps.length-1)).classList.replace('active','done');
      renderResults();
    }
  }
  advance();
});

function renderResults(){
  supplierRunsCount++;
  renderKPIs();
  const wrap = document.getElementById('resultsWrap');
  wrap.innerHTML = `<div class="card-title" style="margin-bottom:10px;">Результаты <span class="demo-flag">Демо-данные — в продакшене подключается к живому веб-поиску</span></div>` +
    parsedItems.map((it,idx)=>{
      const key = matchSupplierKey(it);
      const suppliers = supplierDB[key];
      return `<div class="result-block">
        <div class="result-head"><b>${it}</b><span class="badge badge-blue">найдено поставщиков: ${suppliers.length}</span></div>
        <div class="supplier-grid">
          ${suppliers.map((s,si)=>`
            <div class="supplier-card ${s.best?'best':''}">
              ${s.best?'<span class="best-tag">Лучшее совпадение</span>':''}
              <div class="supplier-name">${s.name}</div>
              <div class="supplier-loc">${s.loc}</div>
              <div class="supplier-price">${s.price}</div>
              <div class="supplier-meta">
                <span>Срок поставки: ${s.lead}</span>
                <span>Мин. партия: ${s.moq}</span>
              </div>
              <div class="stars">${'★'.repeat(Math.round(s.rating))}${'☆'.repeat(5-Math.round(s.rating))} ${s.rating}</div>
              <div class="supplier-actions">
                <button class="btn btn-ghost btn-sm" onclick="showToast('Здесь открылся бы источник: ${s.src}')">Источник</button>
                <button class="btn btn-primary btn-sm" data-add-shortlist="${idx}-${si}">+ В шортлист</button>
              </div>
            </div>`).join('')}
        </div>
      </div>`;
    }).join('');

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
document.getElementById('clearShortlistBtn').addEventListener('click', ()=>{
  shortlist = [];
  document.getElementById('shortlistBar').classList.remove('show');
});
document.getElementById('sendProcurementBtn').addEventListener('click', ()=>{
  showToast(`${shortlist.length} поставщик(ов) отправлено в закупки (демо).`);
});
