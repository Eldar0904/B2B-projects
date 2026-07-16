/* =====================================================================
   ACT GENERATION — component script
   Generates real .docx files (Акт приёма-передачи с ценами / без цен,
   Дефектный акт) entirely client-side — no server, no API, no model.
   Builds a minimal valid OOXML Word package by hand (document.xml,
   styles.xml, rels, content types) and zips it with JSZip.

   Loaded as a plain classic <script src="act-generator.js"></script> —
   no bundler, no ES modules — so it shares the page's global scope
   with the main inline script. It relies on the following globals
   already being defined by the main script / other component scripts
   by the time they're used:
     - currentProjectId     (let, set when a project is opened)
     - projects             (array, kept in sync with Firestore)
     - db                   (firebase.firestore() instance)
     - auth                 (firebase.auth() instance)
     - getUploaderName()    (display name helper from main script)
     - formatFileSize()     (size formatter from main script)
     - uploadProjectBlob(projectId, filename, blob)  (Storage upload helper)
     - showToast(msg)       (toast helper)
     - logActivity(text,by) (activity feed helper)
     - JSZip                (global, loaded via CDN script tag)
   Company details below are placeholders — there is no real company
   letterhead/requisites data wired up yet, so every generated act
   ships with [bracketed] placeholders the user must fill in by hand
   before sending it out.
   ===================================================================== */

const ACT_TYPES = {
  transfer_priced: {
    label: 'Акт приёма-передачи (с ценами)',
    hasPrice: true,
    hasDefect: false,
    notesLabel: 'Примечания',
    signer1Label: 'Сдал (должность, Ф.И.О.)',
    signer2Label: 'Принял (должность, Ф.И.О.)',
    itemsPlaceholder: 'Школьные парты (2-местные) x120, 25000\nСтулья для класса x240, 9500',
  },
  transfer_plain: {
    label: 'Акт приёма-передачи (без цен)',
    hasPrice: false,
    hasDefect: false,
    notesLabel: 'Примечания',
    signer1Label: 'Сдал (должность, Ф.И.О.)',
    signer2Label: 'Принял (должность, Ф.И.О.)',
    itemsPlaceholder: 'Школьные парты (2-местные) x120\nСтулья для класса x240',
  },
  defect: {
    label: 'Дефектный акт',
    hasPrice: false,
    hasDefect: true,
    notesLabel: 'Заключение комиссии',
    signer1Label: 'Председатель комиссии (должность, Ф.И.О.)',
    signer2Label: 'Члены комиссии (по одному на строку)',
    itemsPlaceholder: 'Интерактивная доска 75" x1 — трещина на экране\nСтул x3 — расколота ножка',
  },
};

const COMPANY_PLACEHOLDER =
  '[Название компании]\n' +
  'БИН [__________], адрес: [юридический адрес]\n' +
  'Тел.: [______________], email: [______________]';

/* ===================== line parsing ===================== */
function toNumber(v){
  const n = parseFloat(String(v||'').replace(/\s+/g,'').replace(',', '.'));
  return isNaN(n) ? 0 : n;
}

function parseActLine(raw, typeKey){
  const cfg = ACT_TYPES[typeKey];
  let line = raw.trim();
  if(!line) return null;
  let defect = '';
  let price = '';

  if(cfg.hasDefect){
    const dm = line.match(/^(.+?)\s+[—-]\s+(.+)$/);
    if(dm){ line = dm[1].trim(); defect = dm[2].trim(); }
  }
  if(cfg.hasPrice){
    const pm = line.match(/(?:,|;|\s-\s|\s—\s)\s*([\d\s]{2,}(?:[.,]\d{1,2})?)\s*$/);
    if(pm){ price = pm[1].replace(/\s+/g,'').replace(',', '.'); line = line.slice(0, pm.index).trim(); }
  }
  let qty = '';
  const qm = line.match(/[xх]\s*(\d+(?:[.,]\d+)?)\s*$/i);
  if(qm){ qty = qm[1].replace(',', '.'); line = line.slice(0, qm.index).trim(); }
  line = line.replace(/[,;]\s*$/, '').trim();

  return { name: line || raw.trim(), unit: 'шт.', qty, price, defect };
}

/* ===================== modal state ===================== */
let actItems = [];

function onActTypeChange(){
  const cfg = ACT_TYPES[document.getElementById('actTypeInput').value];
  document.getElementById('actNotesLabel').textContent = cfg.notesLabel;
  document.getElementById('actSigner1Label').textContent = cfg.signer1Label;
  document.getElementById('actSigner2Label').textContent = cfg.signer2Label;
  document.getElementById('actItemsRaw').placeholder = cfg.itemsPlaceholder;
  renderActItemsPreview();
}

function openActModal(){
  if(!currentProjectId){ showToast('Сначала откройте проект — акт привязывается к проекту.'); return; }
  const p = projects.find(x=>x.id===currentProjectId);
  document.getElementById('actTypeInput').value = 'transfer_priced';
  document.getElementById('actNumberInput').value = '';
  document.getElementById('actDateInput').value = new Date().toISOString().slice(0,10);
  document.getElementById('actObjectInput').value = p ? p.name : '';
  document.getElementById('actCustomerInput').value = p && p.client ? p.client : '';
  document.getElementById('actContractorInput').value = COMPANY_PLACEHOLDER;
  document.getElementById('actItemsRaw').value = '';
  document.getElementById('actNotesInput').value = '';
  document.getElementById('actSigner1Input').value = '';
  document.getElementById('actSigner2Input').value = '';
  actItems = [];
  document.getElementById('actItemsPreviewWrap').style.display = 'none';
  onActTypeChange();
  document.getElementById('actModalOverlay').classList.add('show');
}
document.getElementById('generateActBtn').addEventListener('click', openActModal);
document.getElementById('actModalCancel').addEventListener('click', ()=>document.getElementById('actModalOverlay').classList.remove('show'));
document.getElementById('actTypeInput').addEventListener('change', onActTypeChange);

document.getElementById('actParseItemsBtn').addEventListener('click', ()=>{
  const typeKey = document.getElementById('actTypeInput').value;
  const raw = document.getElementById('actItemsRaw').value.trim();
  if(!raw){ showToast('Сначала вставьте список позиций.'); return; }
  actItems = raw.split('\n').map(l=>l.trim()).filter(Boolean).map(l=>parseActLine(l, typeKey)).filter(Boolean);
  document.getElementById('actItemsPreviewWrap').style.display = actItems.length ? 'block' : 'none';
  renderActItemsPreview();
});

function renderActItemsPreview(){
  const table = document.getElementById('actItemsTable');
  if(!actItems.length){ table.innerHTML = ''; return; }
  const typeKey = document.getElementById('actTypeInput').value;
  const cfg = ACT_TYPES[typeKey];

  const head = ['№','Наименование','Ед. изм.','Кол-во']
    .concat(cfg.hasPrice ? ['Цена за ед., ₸','Сумма, ₸'] : [])
    .concat(cfg.hasDefect ? ['Дефект / несоответствие'] : [])
    .concat(['']);

  const rows = actItems.map((it,idx)=>{
    const sum = toNumber(it.qty) * toNumber(it.price);
    return `<tr>
      <td>${idx+1}</td>
      <td><input data-idx="${idx}" data-field="name" value="${escapeHtml(it.name)}" style="width:100%;border:1px solid var(--border);border-radius:6px;padding:5px 7px;font-size:12.5px;"></td>
      <td><input data-idx="${idx}" data-field="unit" value="${escapeHtml(it.unit)}" style="width:60px;border:1px solid var(--border);border-radius:6px;padding:5px 7px;font-size:12.5px;"></td>
      <td><input data-idx="${idx}" data-field="qty" value="${escapeHtml(it.qty)}" style="width:60px;border:1px solid var(--border);border-radius:6px;padding:5px 7px;font-size:12.5px;"></td>
      ${cfg.hasPrice ? `<td><input data-idx="${idx}" data-field="price" value="${escapeHtml(it.price)}" style="width:90px;border:1px solid var(--border);border-radius:6px;padding:5px 7px;font-size:12.5px;"></td>
      <td data-sum-idx="${idx}">${sum?sum.toLocaleString('ru-RU'):'—'}</td>` : ''}
      ${cfg.hasDefect ? `<td><input data-idx="${idx}" data-field="defect" value="${escapeHtml(it.defect)}" style="width:100%;border:1px solid var(--border);border-radius:6px;padding:5px 7px;font-size:12.5px;"></td>` : ''}
      <td><button type="button" class="icon-btn" data-remove-idx="${idx}" title="Удалить">✕</button></td>
    </tr>`;
  }).join('');

  const totalRow = cfg.hasPrice ? `<tr>
      <td colspan="5" style="text-align:right;font-weight:700;">Итого:</td>
      <td id="actItemsTotal" style="font-weight:700;">${actItems.reduce((s,it)=>s+toNumber(it.qty)*toNumber(it.price),0).toLocaleString('ru-RU')}</td>
      <td></td>
    </tr>` : '';

  table.innerHTML = `<thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows}${totalRow}</tbody>`;

  table.querySelectorAll('input[data-idx]').forEach(inp=>{
    inp.addEventListener('input', ()=>{
      const idx = Number(inp.dataset.idx);
      actItems[idx][inp.dataset.field] = inp.value;
      if(inp.dataset.field==='qty' || inp.dataset.field==='price'){
        const sum = toNumber(actItems[idx].qty) * toNumber(actItems[idx].price);
        const sumCell = table.querySelector(`[data-sum-idx="${idx}"]`);
        if(sumCell) sumCell.textContent = sum ? sum.toLocaleString('ru-RU') : '—';
        const totalCell = document.getElementById('actItemsTotal');
        if(totalCell) totalCell.textContent = actItems.reduce((s,it)=>s+toNumber(it.qty)*toNumber(it.price),0).toLocaleString('ru-RU');
      }
    });
  });
  table.querySelectorAll('[data-remove-idx]').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      actItems.splice(Number(btn.dataset.removeIdx), 1);
      renderActItemsPreview();
    });
  });
}

function escapeHtml(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ===================== OOXML (.docx) builder ===================== */
function escapeXml(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function xP(text, opts){
  opts = opts || {};
  const align = opts.align ? `<w:jc w:val="${opts.align}"/>` : '';
  const border = opts.borderBottom ? `<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="000000"/></w:pBdr>` : '';
  const sz = opts.size || 22;
  const rPr = `<w:rPr>${opts.bold?'<w:b/>':''}${opts.italic?'<w:i/>':''}<w:sz w:val="${sz}"/><w:szCs w:val="${sz}"/></w:rPr>`;
  const lines = String(text||'').split('\n');
  const after = opts.after !== undefined ? opts.after : 120;
  return lines.map((line, i)=>
    `<w:p><w:pPr>${align}${border}<w:spacing w:after="${i===lines.length-1?after:40}"/></w:pPr><w:r>${rPr}<w:t xml:space="preserve">${escapeXml(line)}</w:t></w:r></w:p>`
  ).join('');
}

function xTc(text, width, opts){
  opts = opts || {};
  const align = opts.align ? `<w:jc w:val="${opts.align}"/>` : '';
  const bold = opts.bold ? '<w:b/>' : '';
  const sz = opts.size || 20;
  const shd = opts.shade ? `<w:shd w:val="clear" w:fill="${opts.shade}"/>` : '';
  const gridSpan = opts.colSpan ? `<w:gridSpan w:val="${opts.colSpan}"/>` : '';
  return `<w:tc><w:tcPr><w:tcW w:w="${width}" w:type="dxa"/>${gridSpan}${shd}<w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr>${align}<w:spacing w:after="0"/></w:pPr><w:r><w:rPr>${bold}<w:sz w:val="${sz}"/><w:szCs w:val="${sz}"/></w:rPr><w:t xml:space="preserve">${escapeXml(String(text==null?'':text))}</w:t></w:r></w:p></w:tc>`;
}

function xTable(widths, headerCells, bodyRows){
  const grid = widths.map(w=>`<w:gridCol w:w="${w}"/>`).join('');
  const borders = `<w:tblBorders>
    <w:top w:val="single" w:sz="4" w:space="0" w:color="000000"/>
    <w:left w:val="single" w:sz="4" w:space="0" w:color="000000"/>
    <w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/>
    <w:right w:val="single" w:sz="4" w:space="0" w:color="000000"/>
    <w:insideH w:val="single" w:sz="4" w:space="0" w:color="000000"/>
    <w:insideV w:val="single" w:sz="4" w:space="0" w:color="000000"/>
  </w:tblBorders>`;
  const headRow = `<w:tr>${headerCells.map((h,i)=>xTc(h, widths[i], {bold:true, align:'center', shade:'D9D9D9'})).join('')}</w:tr>`;
  return `<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>${borders}<w:tblLook w:val="0000"/></w:tblPr><w:tblGrid>${grid}</w:tblGrid>${headRow}${bodyRows.join('')}</w:tbl>`;
}

function buildActDocumentXml(typeKey, ctx){
  const cfg = ACT_TYPES[typeKey];
  const parts = [];

  // letterhead
  const letterheadLines = ctx.contractor.split('\n');
  parts.push(xP(letterheadLines[0] || '[Название компании]', {bold:true, size:26, align:'center', after:20}));
  if(letterheadLines.length > 1){
    parts.push(xP(letterheadLines.slice(1).join('\n'), {italic:true, size:18, align:'center', after:0}));
  }
  parts.push(xP('', {borderBottom:true, after:200}));

  // title
  parts.push(xP(`${cfg.label.toUpperCase()} № ${ctx.number || 'б/н'} от ${ctx.dateDisplay}`, {bold:true, size:28, align:'center', after:220}));

  // intro
  const intro = cfg.hasDefect
    ? `Комиссия в составе нижеподписавшихся произвела осмотр товарно-материальных ценностей по объекту «${ctx.objectName}» и составила настоящий акт о выявлении указанных ниже дефектов / несоответствий.`
    : `Мы, нижеподписавшиеся, составили настоящий акт о том, что Исполнитель передал, а Заказчик принял по объекту «${ctx.objectName}» следующие товарно-материальные ценности:`;
  parts.push(xP(intro, {after:160}));

  parts.push(xP(`Заказчик: ${ctx.customer || '[наименование заказчика]'}`, {after:40}));
  parts.push(xP(`Исполнитель: ${ctx.contractor.split('\n').join(', ')}`, {after:40}));
  parts.push(xP(`Объект: ${ctx.objectName}`, {after:220}));

  // items table
  let widths, head;
  if(typeKey === 'transfer_priced'){
    widths = [500,3705,900,900,1500,1850];
    head = ['№','Наименование','Ед. изм.','Кол-во','Цена за ед., ₸','Сумма, ₸'];
  } else if(typeKey === 'transfer_plain'){
    widths = [500,4105,1000,1200,2550];
    head = ['№','Наименование','Ед. изм.','Кол-во','Примечание'];
  } else {
    widths = [500,3105,900,1000,3850];
    head = ['№','Наименование','Ед. изм.','Кол-во','Дефект / несоответствие'];
  }

  let total = 0;
  const bodyRows = ctx.items.map((it,idx)=>{
    const cells = [xTc(idx+1, widths[0], {align:'center'}), xTc(it.name, widths[1])];
    cells.push(xTc(it.unit||'шт.', widths[2], {align:'center'}));
    cells.push(xTc(it.qty||'—', widths[3], {align:'center'}));
    if(typeKey === 'transfer_priced'){
      const qty = toNumber(it.qty), price = toNumber(it.price);
      const sum = qty*price;
      total += sum;
      cells.push(xTc(price ? price.toLocaleString('ru-RU') : '—', widths[4], {align:'right'}));
      cells.push(xTc(sum ? sum.toLocaleString('ru-RU') : '—', widths[5], {align:'right'}));
    } else if(typeKey === 'transfer_plain'){
      cells.push(xTc('', widths[4]));
    } else {
      cells.push(xTc(it.defect||'—', widths[4]));
    }
    return `<w:tr>${cells.join('')}</w:tr>`;
  });

  if(typeKey === 'transfer_priced'){
    bodyRows.push(`<w:tr>${xTc('Итого:', widths[0]+widths[1]+widths[2]+widths[3], {bold:true, align:'right', colSpan:4})}${xTc('', widths[4])}${xTc(total.toLocaleString('ru-RU'), widths[5], {bold:true, align:'right'})}</w:tr>`);
  }

  parts.push(xTable(widths, head, bodyRows));
  parts.push(xP('', {after:200}));

  if(ctx.notes){
    parts.push(xP(`${cfg.notesLabel}: ${ctx.notes}`, {after:240}));
  }

  // signatures
  if(cfg.hasDefect){
    parts.push(xP(`Председатель комиссии: ___________________  ${ctx.signer1 || '[Ф.И.О.]'}`, {after:160}));
    parts.push(xP('Члены комиссии:', {after:80}));
    const members = (ctx.signer2 || '[Ф.И.О.]').split('\n').filter(Boolean);
    members.forEach(m=>parts.push(xP(`___________________  ${m}`, {after:80})));
  } else {
    parts.push(xP(`Сдал: ___________________  ${ctx.signer1 || '[Ф.И.О.]'}`, {after:200}));
    parts.push(xP(`Принял: ___________________  ${ctx.signer2 || '[Ф.И.О.]'}`, {after:0}));
  }

  const body = parts.join('') +
    `<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>`;

  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:body>${body}</w:body>
</w:document>`;
}

const CONTENT_TYPES_XML = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>`;

const ROOT_RELS_XML = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>`;

const DOC_RELS_XML = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`;

const STYLES_XML = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults>
  <w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="ru-RU"/></w:rPr></w:rPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
</w:styles>`;

function buildActDocxBlob(typeKey, ctx){
  const zip = new JSZip();
  zip.file('[Content_Types].xml', CONTENT_TYPES_XML);
  zip.folder('_rels').file('.rels', ROOT_RELS_XML);
  const wordFolder = zip.folder('word');
  wordFolder.file('document.xml', buildActDocumentXml(typeKey, ctx));
  wordFolder.file('styles.xml', STYLES_XML);
  wordFolder.folder('_rels').file('document.xml.rels', DOC_RELS_XML);
  return zip.generateAsync({ type:'blob', mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
}

function triggerFileDownload(blob, filename){
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(()=>{ document.body.removeChild(a); URL.revokeObjectURL(url); }, 200);
}

function fmtDateDisplay(isoDate){
  if(!isoDate) return '—';
  const [y,m,d] = isoDate.split('-');
  return `${d}.${m}.${y}`;
}

document.getElementById('actModalGenerate').addEventListener('click', ()=>{
  if(!actItems.length){ showToast('Сначала добавьте и разберите хотя бы одну позицию.'); return; }
  const typeKey = document.getElementById('actTypeInput').value;
  const cfg = ACT_TYPES[typeKey];
  const dateRaw = document.getElementById('actDateInput').value || new Date().toISOString().slice(0,10);
  const ctx = {
    number: document.getElementById('actNumberInput').value.trim(),
    dateDisplay: fmtDateDisplay(dateRaw),
    objectName: document.getElementById('actObjectInput').value.trim() || '[объект]',
    customer: document.getElementById('actCustomerInput').value.trim(),
    contractor: document.getElementById('actContractorInput').value.trim() || COMPANY_PLACEHOLDER,
    items: actItems,
    notes: document.getElementById('actNotesInput').value.trim(),
    signer1: document.getElementById('actSigner1Input').value.trim(),
    signer2: document.getElementById('actSigner2Input').value.trim(),
  };

  buildActDocxBlob(typeKey, ctx).then(blob=>{
    const safeNumber = (ctx.number || 'bn').replace(/[^\wа-яА-Я0-9-]/g,'_');
    const typeShort = { transfer_priced:'priem-peredachi-cena', transfer_plain:'priem-peredachi', defect:'defektny' }[typeKey];
    const filename = `Akt_${typeShort}_No${safeNumber}_${dateRaw}.docx`;
    triggerFileDownload(blob, filename);

    const by = (typeof getUploaderName === 'function')
      ? getUploaderName()
      : ((auth.currentUser && auth.currentUser.email) ? auth.currentUser.email.split('@')[0] : '—');
    const saveDoc = meta => db.collection('documents').add({
      projectId: currentProjectId,
      name: filename,
      category: 'Акт',
      by,
      date: dateRaw,
      size: formatFileSize(blob.size),
      ...meta,
    });
    const persist = (typeof uploadProjectBlob === 'function' && currentProjectId)
      ? uploadProjectBlob(currentProjectId, filename, blob).then(({ url, storagePath })=>saveDoc({ url, storagePath, mimeType: blob.type || '' }))
      : saveDoc({ size: `${Math.max(1, Math.round(blob.size/1024))} КБ` });

    persist.then(()=>{
      logActivity(`сформировал(а) «${cfg.label}» № ${ctx.number || 'б/н'}`, by);
    }).catch(err=>console.error('Ошибка записи акта в реестр документов:', err));

    showToast('Акт сформирован и скачан. Не забудьте заполнить реквизиты компании перед отправкой.');
    document.getElementById('actModalOverlay').classList.remove('show');
  }).catch(err=>{
    console.error('Ошибка генерации .docx:', err);
    showToast('Не удалось сформировать документ: '+err.message);
  });
});
