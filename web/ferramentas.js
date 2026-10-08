/* ferramentas.js — mostra, sob a resposta, as ferramentas que o modelo usou (memória, grafo,
   projetos, conversas, documentos) e permite desfazer o que ele gravou.
   O /chat embute cada evento no stream de texto entre dois caracteres \x1e; aqui eles são
   separados do texto. Carregar depois de ajustes.js (usa AjustesUI.t e AtlasUI). */
(function(){
'use strict';
var M='\x1e';
var S={
  pt:{tlN:'Ferramentas usadas ({n})',tlRun:'usando {f}…',tlUndo:'Desfazer',tlUndone:'desfeito',tlUndoT:'Desfazer esta ação?',
      tlUndoM:'A memória/grafo volta a como estava antes desta ação do modelo.',tlConf:'Esta memória foi editada depois. Desfazer mesmo assim?',
      tlErr:'Não deu para desfazer.',tlOk:'Ação desfeita.',tlFail:'falhou',tlLog:'Ver registro',
      f_search_memories:'Buscou memórias',f_save_memory:'Salvou memória',f_update_memory:'Editou memória',f_pin_memory:'Fixou memória',
      f_graph_search:'Buscou no grafo',f_add_concept:'Criou conceito',f_link_concepts:'Ligou conceitos',f_list_projects:'Listou projetos',
      f_recall_conversation:'Lembrou conversas',f_list_documents:'Listou documentos',f_read_document:'Leu documento',
      tlLogT:'Ações do modelo',tlLogEmpty:'Nenhuma ação de escrita ainda.'},
  en:{tlN:'Tools used ({n})',tlRun:'using {f}…',tlUndo:'Undo',tlUndone:'undone',tlUndoT:'Undo this action?',
      tlUndoM:'Memory/graph goes back to how it was before this model action.',tlConf:'This memory was edited afterwards. Undo anyway?',
      tlErr:'Could not undo.',tlOk:'Action undone.',tlFail:'failed',tlLog:'View log',
      f_search_memories:'Searched memories',f_save_memory:'Saved memory',f_update_memory:'Edited memory',f_pin_memory:'Pinned memory',
      f_graph_search:'Searched graph',f_add_concept:'Added concept',f_link_concepts:'Linked concepts',f_list_projects:'Listed projects',
      f_recall_conversation:'Recalled conversations',f_list_documents:'Listed documents',f_read_document:'Read document',
      tlLogT:'Model actions',tlLogEmpty:'No write actions yet.'},
  es:{tlN:'Herramientas usadas ({n})',tlRun:'usando {f}…',tlUndo:'Deshacer',tlUndone:'deshecho',tlUndoT:'¿Deshacer esta acción?',
      tlUndoM:'La memoria/grafo vuelve a como estaba antes de esta acción del modelo.',tlConf:'Esta memoria se editó después. ¿Deshacer igual?',
      tlErr:'No se pudo deshacer.',tlOk:'Acción deshecha.',tlFail:'falló',tlLog:'Ver registro',
      f_search_memories:'Buscó memorias',f_save_memory:'Guardó memoria',f_update_memory:'Editó memoria',f_pin_memory:'Fijó memoria',
      f_graph_search:'Buscó en el grafo',f_add_concept:'Creó concepto',f_link_concepts:'Vinculó conceptos',f_list_projects:'Listó proyectos',
      f_recall_conversation:'Recordó conversaciones',f_list_documents:'Listó documentos',f_read_document:'Leyó documento',
      tlLogT:'Acciones del modelo',tlLogEmpty:'Aún no hay acciones de escritura.'}
};
// junta as frases ao dicionário do painel de ajustes (mesmo idioma, mesma função t)
if(window.AjustesUI&&AjustesUI._D){Object.keys(S).forEach(function(l){AjustesUI._D[l]=AjustesUI._D[l]||{};
  Object.keys(S[l]).forEach(function(k){if(!(k in AjustesUI._D[l]))AjustesUI._D[l][k]=S[l][k];});});}
function t(k,v){if(window.AjustesUI)return AjustesUI.t(k,v);var s=S.pt[k]||k;if(v)for(var x in v)s=s.split('{'+x+'}').join(v[x]);return s;}
function el(tag,attrs,kids){var e=document.createElement(tag);
  if(attrs)Object.keys(attrs).forEach(function(k){var v=attrs[k];if(v==null)return;
    if(k==='class')e.className=v;else if(k==='text')e.textContent=v;else if(k.slice(0,2)==='on')e[k]=v;else e.setAttribute(k,v);});
  (kids||[]).forEach(function(c){if(c!=null)e.append(c);});return e;}

// estilo isolado, nos tokens de ui.css (mesmo visual do bloco "Memórias usadas")
var css=el('style',{text:
  '.ferr{margin:0 0 10px;font-size:var(--fs-sm);color:var(--dim)}'+
  '.ferr>summary{cursor:pointer;user-select:none;list-style:none;display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 8px 0 6px;margin-left:-6px;'+
    'border-radius:var(--r-sm);transition:background var(--dur),color var(--dur)}'+
  '.ferr>summary::-webkit-details-marker{display:none}'+
  '.ferr>summary:hover{color:var(--txt);background:var(--hover)}'+
  '.ferr>summary .fchev{display:inline-flex;color:var(--faint);transition:transform var(--dur) var(--ease)}.ferr[open]>summary .fchev{transform:rotate(90deg)}'+
  '.ferr>summary .fnw{color:var(--faint);font-variant-numeric:tabular-nums}'+
  '.ferr ul{list-style:none;margin:6px 0 2px;padding:0;display:flex;flex-direction:column;gap:6px}'+
  '.ferr li{display:flex;gap:10px;align-items:center;padding:7px 6px 7px 10px;background:var(--panel);border:1px solid var(--line);border-radius:10px}'+
  '.ferr .fi{width:26px;height:26px;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;background:var(--accent-soft);color:var(--accent)}'+
  '.ferr li.w .fi{background:color-mix(in srgb,var(--good) 14%,transparent);color:var(--good)}'+
  '.ferr li.x .fi{background:color-mix(in srgb,var(--danger) 12%,transparent);color:var(--danger)}'+
  '.ferr .ft{flex:1;min-width:0;display:flex;flex-direction:column;gap:1px}'+
  '.ferr .fn{font-weight:var(--fw-medium);color:var(--txt);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ferr .fr{word-break:break-word;font-size:var(--fs-xs);line-height:1.45}'+
  '.ferr .ferr-x{color:var(--danger)}.ferr li.desfeita{opacity:.6}.ferr li.desfeita .fr{text-decoration:line-through}'+
  '.ferr .fu{display:inline-flex;align-items:center;gap:5px;flex-shrink:0;height:28px;padding:0 10px;font:inherit;font-size:var(--fs-xs);font-weight:var(--fw-medium);'+
    'border:1px solid var(--line2);background:var(--panel2);color:var(--txt);border-radius:var(--r-sm);cursor:pointer;transition:background var(--dur)}'+
  '.ferr .fu:hover{background:var(--panel3)}.ferr .fu:disabled{opacity:.5;cursor:default}'+
  '.ferr .fdone{display:inline-flex;align-items:center;gap:4px;flex-shrink:0;font-size:11px;color:var(--faint);padding-right:4px}'+
  '.ferr .run{font-style:italic}'+
  '.ferr.log ul{margin:0}.ferr.log .fr{color:var(--dim)}'});
document.head.appendChild(css);
function ico(n,sz){return (window.AtlasIcons&&AtlasIcons.el(n,sz||14))||null;}
var ICO={search_memories:'search',save_memory:'brain',update_memory:'pencil',pin_memory:'pin',graph_search:'graph',add_concept:'plus',
  link_concepts:'link',list_projects:'folder',recall_conversation:'message',list_documents:'file-text',read_document:'file'};
function botaoDesfazer(){var b=el('button',{type:'button',class:'fu'});if(window.AtlasIcons)AtlasIcons.label(b,'rotate-ccw',t('tlUndo'),13);else b.textContent=t('tlUndo');return b;}
function feito(){return el('span',{class:'fdone'},[ico('check',12),el('span',{text:t('tlUndone')})]);}
// linha de uma ação: ícone da ferramenta, nome e resumo
function linhaAcao(tool,resumo,ok,escrita,titulo){
  var li=el('li',{class:(escrita?'w':'')+(ok?'':' x')});
  li.append(el('span',{class:'fi'},[ico(ok?(ICO[tool]||'wrench'):'alert-circle',14)]));
  li.append(el('span',{class:'ft'},[el('span',{class:'fn',text:nomeF(tool)}),resumo?el('span',{class:'fr'+(ok?'':' ferr-x'),text:resumo,title:titulo||null}):null]));
  return li;
}

// separa o texto da resposta dos eventos de ferramenta
function separar(acc){
  var partes=String(acc||'').split(M),texto='',eventos=[];
  if(partes.length%2===0)partes.pop();          // marcador ainda chegando: espera o resto
  for(var i=0;i<partes.length;i++){
    if(i%2===0)texto+=partes[i];
    else{try{eventos.push(JSON.parse(partes[i]));}catch(e){}}
  }
  return {texto:texto,eventos:eventos};
}
function nomeF(f){var k='f_'+f;var s=t(k);return s===k?f:s;}

async function desfazer(ev,li,btn){
  var ok=await AtlasUI.form({title:t('tlUndoT'),message:(ev.resumo?'“'+ev.resumo+'”\n\n':'')+t('tlUndoM'),okText:t('tlUndo'),danger:true});
  if(!ok)return;
  btn.disabled=true;
  async function pedir(forcar){
    var r=await fetch('/api/ferramentas/desfazer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:ev.acao,forcar:!!forcar})});
    var d={};try{d=await r.json();}catch(e){}
    return d;
  }
  try{
    var d=await pedir(false);
    if(!d.ok&&d.erro==='conflito'){
      if(await AtlasUI.form({title:t('tlUndoT'),message:t('tlConf'),okText:t('tlUndo'),danger:true}))d=await pedir(true);
      else{btn.disabled=false;return;}
    }
    if(d.ok||d.erro==='ja_desfeita'){li.classList.add('desfeita');btn.replaceWith(feito());
      if(d.ok)AtlasUI.toast(t('tlOk'),{kind:'ok'});}
    else{btn.disabled=false;AtlasUI.toast(t('tlErr'),{kind:'err'});}
  }catch(e){btn.disabled=false;AtlasUI.toast(t('tlErr'),{kind:'err'});}
}

// <details> com as ferramentas usadas, logo acima do texto da resposta
function bloco(r,eventos,rodando){
  var bub=r.bubble||(r.body&&r.body.closest('.bubble'));if(!bub)return;
  var d=bub.querySelector('details.ferr');
  if(!eventos||!eventos.length){if(d)d.remove();return;}
  var aberto=d?d.open:false;
  var novo=el('details',{class:'ferr'});novo.open=aberto;
  novo.append(el('summary',{},[el('span',{class:'fchev'},[ico('chevron-right',14)]),ico('wrench',14),el('span',{text:t('tlN',{n:eventos.length})})]));
  var ul=el('ul');
  eventos.forEach(function(ev){
    var txt=ev.ok?(ev.resumo||''):(t('tlFail')+(ev.erro?': '+ev.erro:''));
    var li=linhaAcao(ev.tool,txt,ev.ok,!!ev.acao,JSON.stringify(ev.args||{}));
    if(ev.ok&&ev.acao){var b=botaoDesfazer();b.onclick=function(){desfazer(ev,li,b);};li.append(b);}
    ul.append(li);
  });
  novo.append(ul);
  if(d)d.replaceWith(novo);else{var body=r.body||bub.querySelector('.body');bub.insertBefore(novo,body||null);}
}

// pinta o streaming: texto em markdown + bloco de ferramentas; devolve o texto limpo
function pintar(r,acc,md){
  var s=separar(acc);
  r.body.innerHTML=md(s.texto);
  bloco(r,s.eventos);
  return s.texto;
}

// registro de ações (aberto pelo painel de ajustes ou console)
async function registro(){
  var d={acoes:[]};try{d=await(await fetch('/api/ferramentas?limite=100')).json();}catch(e){}
  AtlasUI.modal({title:t('tlLogT'),wide:true,build:function(box,fechar){
    var ul=el('ul');
    if(!d.acoes.length)box.append(el('div',{class:'ui-empty',text:t('tlLogEmpty')}));
    d.acoes.forEach(function(a){
      var li=linhaAcao(a.tool,[a.resumo||'',(a.ts||'').replace('T',' ').slice(0,16),a.modelo||''].filter(Boolean).join(' · '),true,true);
      if(a.desfeita)li.classList.add('desfeita');
      if(!a.desfeita){var b=botaoDesfazer();b.onclick=function(){desfazer({acao:a.id,resumo:a.resumo},li,b);};li.append(b);}
      else li.append(feito());
      ul.append(li);
    });
    var w=el('div',{class:'ferr log'});w.append(ul);box.append(w);
    box.append(el('div',{class:'ui-actions'},[el('button',{class:'ui-btn',type:'button',text:'OK',onclick:function(){fechar(null);}})]));
  }});
}

window.AtlasTools={separar:separar,pintar:pintar,bloco:bloco,registro:registro,MARCA:M};
})();
