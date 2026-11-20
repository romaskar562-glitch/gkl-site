'use strict';
const {items,sections}=window.PRICE_DATA;
const byId=new Map(items.map(item=>[item.id,item]));
const selected=new Map();
const $=id=>document.getElementById(id);
const money=value=>new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',maximumFractionDigits:2}).format(value);
const groups={gkl:['21','27','28'],paint:['34','35','37'],detail:['2','3','18','29','30','31','32','36','38','41','42']};
const sectionNames={'18':'Отверстия в ГКЛ / ГВЛ','21':'ГКЛ в ванной комнате','27':'ГКЛ — потолки','28':'ГКЛ — стены и перегородки','29':'Шумоизоляция','30':'Примыкания подоконников','31':'Откосы','32':'Скрытые двери и теневые плинтусы','34':'Подготовка потолков','35':'Подготовка стен','36':'Декоративные элементы','37':'Обои и окраска','38':'Отверстия в потолке','41':'Плинтусы','42':'Отверстия и защита поверхностей'};
let selectedOnly=false;
function element(tag,className,text){
 const node=document.createElement(tag);
 if(className)node.className=className;
 if(text!==undefined)node.textContent=text;
 return node;
}
function isIndividual(item){return item.price===null||item.individual||item.id==='37.5.';}
function lineTotal(item,state){
 if(isIndividual(item)||!Number.isFinite(state.quantity)||state.quantity<=0)return null;
 return Math.round(item.price*state.quantity*100)/100;
}
function calculation(){
 let cents=0,unresolved=0,hasFrom=false;
 for(const [id,state] of selected){
  const item=byId.get(id),total=lineTotal(item,state);
  if(total===null)unresolved++;else cents+=Math.round(total*100);
  hasFrom=hasFrom||item.from;
 }
 return {total:cents/100,unresolved,hasFrom};
}
function filterItems(){
 const query=$('search').value.trim().toLocaleLowerCase('ru');
 const direction=$('direction').value;
 return items.filter(item=>(direction==='all'||groups[direction].includes(item.section))&&(!selectedOnly||selected.has(item.id))&&(!query||(item.name+' '+item.code).toLocaleLowerCase('ru').includes(query)));
}
function renderCatalog(){
 const filtered=filterItems();
 const openIds=new Set([...document.querySelectorAll('.group[open]')].map(node=>node.dataset.section));
 $('catalog').replaceChildren();
 $('no-results').hidden=filtered.length>0;
 sections.forEach(section=>{
  const matching=filtered.filter(item=>item.section===section.id);
  if(!matching.length)return;
  const details=element('details','group');
  details.dataset.section=section.id;
  details.open=!!$('search').value.trim()||selectedOnly||$('direction').value!=='all'||openIds.has(section.id);
  const summary=element('summary','',sectionNames[section.id]||section.name);
  summary.append(element('span','',matching.length+' поз.'));
  details.append(summary);
  matching.forEach(item=>{
   const row=element('div','work-row');
   const check=element('input');check.type='checkbox';check.id='choose-'+item.id;check.checked=selected.has(item.id);
   const body=element('div');
   const label=element('label','work-name',item.name);label.htmlFor=check.id;
   body.append(label,element('p','work-price',isIndividual(item)?'По согласованию':(item.from?'от ':'')+money(item.price)+' / '+item.unit));
   const controls=element('div','work-controls');controls.hidden=!check.checked;
   const qtyLabel=element('label','', 'Объём, '+(selected.get(item.id)?.unit||item.unit));
   const qty=element('input');qty.type='number';qty.min='0';qty.max='1000000';qty.step=/шт|компл/.test(item.unit)?'1':'0.01';qty.inputMode='decimal';qty.placeholder='0';qty.setAttribute('aria-label','Объём — '+item.name);
   qty.value=selected.get(item.id)?.quantity||'';
   qtyLabel.append(qty);controls.append(qtyLabel);
   if(['34','35'].includes(item.section)&&item.unit==='м²'){
    const unitLabel=element('label','','Поверхность');const unit=element('select');
    for(const [value,text] of [['м²','Ширина от 1 м'],['пог.м.','Уже 1 м']]){const option=element('option','',text);option.value=value;unit.append(option);}
    unit.value=selected.get(item.id)?.unit||item.unit;
    unit.addEventListener('change',()=>{selected.get(item.id).unit=unit.value;qtyLabel.firstChild.textContent='Объём, '+unit.value;updateSummary();});
    unitLabel.append(unit);controls.append(unitLabel);
   }
   check.addEventListener('change',()=>{
    if(check.checked){selected.set(item.id,{quantity:0,unit:item.unit});qty.value='';}else selected.delete(item.id);
    controls.hidden=!check.checked;
    updateSummary();
    if(selectedOnly&&!check.checked)renderCatalog();
   });
   qty.addEventListener('input',()=>{
    const value=qty.valueAsNumber;
    const valid=qty.validity.valid&&Number.isFinite(value)&&value>0;
    selected.get(item.id).quantity=valid?value:0;
    qty.setAttribute('aria-invalid',String(qty.value!==''&&!valid));
    updateSummary();
   });
   body.append(controls);
   if(item.id==='37.5.')body.append(element('p','work-note','Стоимость замены обоев уточняется с учётом цены рулона.'));
   if(item.section==='37')body.append(element('p','work-note','Расценка для высоты до 2,7 м.'));
   if(item.section==='29')body.append(element('p','work-note','Условия высоты и состав системы указаны в названии работы.'));
   row.append(check,body);details.append(row);
  });
  $('catalog').append(details);
 });
}
function updateSummary(){
 const result=calculation();
 $('total').textContent=selected.size?((result.hasFrom||result.unresolved)?'от ':'')+money(result.total):'—';
 if(selected.size&&result.unresolved===selected.size)$('total').textContent='Уточним на консультации';
 $('total-label').textContent=selected.size?'Стоимость выбранных работ':'Выберите работы';
 $('selected-count').textContent=selected.size?'Выбрано позиций — '+selected.size:'Добавьте объёмы для расчёта';
 $('estimate-list').replaceChildren();
 for(const [id,state] of selected){
  const item=byId.get(id),line=element('div','estimate-item');
  const remove=element('button','','×');remove.type='button';remove.setAttribute('aria-label','Удалить — '+item.name);
  remove.addEventListener('click',()=>{selected.delete(id);renderCatalog();updateSummary();});
  line.append(remove,element('div','',item.name));
  const total=lineTotal(item,state);
  line.append(element('span','',(state.quantity||'Объём не указан')+(state.quantity?' '+state.unit:'')+' · '+(total===null?'требует уточнения':money(total))));
  $('estimate-list').append(line);
 }
 const warnings=[];
 if(result.unresolved)warnings.push('Требуют уточнения цены или объёма — '+result.unresolved+' поз.');
 const height=Number($('height').value);
 if(height>2.7&&[...selected.keys()].some(id=>byId.get(id).section==='37'))warnings.push('Высота выше 2,7 м. Стоимость окраски и обоев уточним после консультации.');
 if(height>3&&[...selected.keys()].some(id=>byId.get(id).section==='29'))warnings.push('Для шумоизоляции выше 3 м стоимость уточняется.');
 $('estimate-warning').textContent=warnings.join(' ');
 $('download').disabled=!selected.size;$('reset').disabled=!selected.size;
 $('request-result').hidden=true;
 $('download-status').textContent='';
}
function estimateText(includeContact=false){
 const lines=['ГКЛ Премиум','Предварительный расчёт работ','Объект — '+$('property').value];
 for(const [id,title] of [['location','Место'],['height','Высота, м'],['timing','Сроки']]){if($(id).value.trim())lines.push(title+' — '+$(id).value.trim());}
 if(includeContact){lines.push('Имя — '+$('client-name').value.trim(),'Телефон — '+$('client-phone').value.trim());if($('comment').value.trim())lines.push('Комментарий — '+$('comment').value.trim());}
 lines.push('');
 for(const [id,state] of selected){
  const item=byId.get(id),total=lineTotal(item,state);
  lines.push((item.code?item.code+' ':'')+item.name, 'Объём — '+(state.quantity||'уточнить')+' '+state.unit+'; ставка — '+(isIndividual(item)?'по согласованию':(item.from?'от ':'')+money(item.price))+'; сумма — '+(total===null?'уточнить':money(total)),'');
 }
 const result=calculation();
 lines.push('Итого по рассчитанным позициям — '+money(result.total));
 if(result.unresolved)lines.push('Требуют уточнения — '+result.unresolved+' поз.');
 if($('estimate-warning').textContent)lines.push($('estimate-warning').textContent);
 lines.push('Материалы и дополнительные условия согласуются на консультации.','Григорий +7 993 435 1232','Георгий +7 910 796 1121');
 if(window.materialEstimateText)lines.push('',window.materialEstimateText());
 return lines.join('\n');
}
function download(text,name){
 const url=URL.createObjectURL(new Blob(['\uFEFF'+text],{type:'text/plain;charset=utf-8'}));
 const link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$('search').addEventListener('input',renderCatalog);$('direction').addEventListener('change',renderCatalog);
$('selected-filter').addEventListener('click',()=>{selectedOnly=!selectedOnly;$('selected-filter').setAttribute('aria-pressed',String(selectedOnly));renderCatalog();});
document.querySelectorAll('[data-service]').forEach(link=>link.addEventListener('click',()=>{$('direction').value=link.dataset.service;selectedOnly=false;$('selected-filter').setAttribute('aria-pressed','false');$('search').value='';renderCatalog();}));
for(const id of ['property','height','location','timing','client-name','client-phone','comment','recipient'])$(id).addEventListener('input',updateSummary);
$('reset').addEventListener('click',()=>{selected.clear();renderCatalog();updateSummary();});
$('download').addEventListener('click',()=>{download(estimateText(),'ГКЛ-Премиум-смета.txt');$('download-status').textContent='Расчёт подготовлен для скачивания.';});
$('request-form').addEventListener('submit',event=>{
 event.preventDefault();
 const text=(window.quickEstimateText?.()||'')+'\n\n'+estimateText(true);
 $('message-preview').value=text;
 $('telegram-link').href='https://t.me/'+$('recipient').value+'?text='+encodeURIComponent(text);
 $('email-link').href='mailto:romaskar562@gmail.com?subject='+encodeURIComponent('Заявка — ГКЛ Премиум')+'&body='+encodeURIComponent(text);
 const ios=/iPhone|iPad|iPod/.test(navigator.userAgent);
 $('sms-link').href='sms:'+$('recipient').value+(ios?'&':'?')+'body='+encodeURIComponent(text);
 $('request-result').hidden=false;
 $('request-status').textContent='Заявка ещё не отправлена. Выберите способ передачи.';
 $('message-preview').focus();
});
$('copy-request').addEventListener('click',async()=>{
 try{await navigator.clipboard.writeText($('message-preview').value);$('request-status').textContent='Текст скопирован. Вставьте его в сообщение выбранному специалисту.';}
 catch{$('message-preview').focus();$('message-preview').select();$('request-status').textContent='Выделен текст заявки. Скопируйте его сочетанием Ctrl+C или через меню.';}
});
$('save-request').addEventListener('click',()=>download($('message-preview').value,'ГКЛ-Премиум-заявка.txt'));
renderCatalog();updateSummary();

// Price cards select their operation and expose its quantity control.
document.querySelectorAll('[data-work]').forEach(link=>link.addEventListener('click',()=>{const id=link.dataset.work,item=byId.get(id);if(!item)return;$('direction').value='all';$('search').value=item.name;selectedOnly=false;$('selected-filter').setAttribute('aria-pressed','false');if(!selected.has(id))selected.set(id,{quantity:0,unit:item.unit});renderCatalog();updateSummary();setTimeout(()=>{const row=document.getElementById('choose-'+id)?.closest('.work-row');row?.querySelector('input[type=number]')?.focus();},100);}));
