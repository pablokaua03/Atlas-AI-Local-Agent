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

// estilo mínimo e isolado (usa as variáveis de tema da página)
var css=el('style',{text:
  '.ferr{margin:0 0 8px;font-size:12.5px;color:var(--dim)}'+
  '.ferr>summary{cursor:pointer;user-select:none;list-style:none}.ferr>summary::-webkit-details-marker{display:none}'+
  '.ferr>summary::before{content:"\\203A";display:inline-block;width:12px;transition:transform .15s}.ferr[open]>summary::before{transform:rotate(90deg)}'+
  '.ferr>summary:hover{color:var(--txt)}.ferr ul{list-style:none;margin:6px 0 2px;padding:0;display:flex;flex-direction:column;gap:5px}'+
  '.ferr li{display:flex;gap:8px;align-items:center;padding:5px 8px;background:var(--bg);border-radius:8px}'+
  '.ferr .fn{font-weight:600;color:var(--txt);white-space:nowrap}.ferr .fr{flex:1;word-break:break-word}'+
  '.ferr .ferr-x{color:#e5484d}.ferr li.desfeita .fr{text-decoration:line-through}'+
  '.ferr button{font:inherit;font-size:11.5px;border:1px solid var(--line);background:transparent;color:var(--dim);border-radius:7px;padding:2px 8px;cursor:pointer}'+
  '.ferr button:hover{color:var(--txt);border-color:var(--accent)}.ferr .run{font-style:italic}'});
document.head.appendChild(css);

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
    if(d.ok||d.erro==='ja_desfeita'){li.classList.add('desfeita');btn.replaceWith(el('span',{text:t('tlUndone')}));
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
  novo.append(el('summary',{text:t('tlN',{n:eventos.length})}));
  var ul=el('ul');
  eventos.forEach(function(ev){
    var li=el('li');
    li.append(el('span',{class:'fn',text:nomeF(ev.tool)}));
    var txt=ev.ok?(ev.resumo||''):(t('tlFail')+(ev.erro?': '+ev.erro:''));
    li.append(el('span',{class:'fr'+(ev.ok?'':' ferr-x'),text:txt,title:JSON.stringify(ev.args||{})}));
    if(ev.ok&&ev.acao){var b=el('button',{type:'button',text:t('tlUndo')});b.onclick=function(){desfazer(ev,li,b);};li.append(b);}
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
    var ul=el('ul',{class:'ferr'});ul.style.listStyle='none';ul.style.padding='0';
    if(!d.acoes.length)box.append(el('div',{class:'ui-empty',text:t('tlLogEmpty')}));
    d.acoes.forEach(function(a){
      var li=el('li',{class:a.desfeita?'desfeita':''});
      li.append(el('span',{class:'fn',text:nomeF(a.tool)}),el('span',{class:'fr',text:(a.resumo||'')+' · '+(a.ts||'').replace('T',' ')+' · '+(a.modelo||'')}));
      if(!a.desfeita){var b=el('button',{type:'button',text:t('tlUndo')});b.onclick=function(){desfazer({acao:a.id,resumo:a.resumo},li,b);};li.append(b);}
      else li.append(el('span',{text:t('tlUndone')}));
      ul.append(li);
    });
    var w=el('div',{class:'ferr'});w.append(ul);box.append(w);
    box.append(el('div',{class:'ui-actions'},[el('button',{class:'ui-btn',type:'button',text:'OK',onclick:function(){fechar(null);}})]));
  }});
}

window.AtlasTools={separar:separar,pintar:pintar,bloco:bloco,registro:registro,MARCA:M};
})();
