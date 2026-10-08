/* modelos.js — catálogo completo de modelos com recomendação para ESTA máquina.
   Usado pela seção Modelo do painel de ajustes (ajustes.js chama AtlasModelos.*):
   - filtros: Recomendados, Todos, por nível (leve … enorme), Ferramentas, Visão, Código, Embeddings
   - visão "Recomendados para o seu PC" (rápido, equilibrado, mais inteligente, visão, código, busca)
   - extras em cada cartão: nível, ferramentas sim/não, MoE, pontos fortes, medido x pesquisado,
     variantes de quantização com selo de encaixe
   - hardware detectado (GPUs, VRAM, RAM, CPU) + ajuste manual
   - seção de ferramentas do modelo (ligar, permitir escrita, registro de ações)
   Carregar depois de ajustes.js. */
(function(){
'use strict';
var S={
  pt:{f_recomendados:'Recomendados',f_ferramentas:'Ferramentas',n_leve:'Leve',n_moderado:'Moderado',n_pesado:'Pesado',n_grande:'Grande',n_enorme:'Enorme',
      fn_leve:'Leves (≤4B)',fn_moderado:'Moderados (7–9B)',fn_pesado:'Pesados (12–14B)',fn_grande:'Grandes (20–35B, MoE)',fn_enorme:'Enormes (70B+)',
      recHdr:'Recomendados para o seu PC',recNone:'Não deu para detectar o hardware. Informe-o em “Ajustar hardware”.',
      p_rapido:'Mais rápido',p_equilibrado:'Melhor que cabe inteiro',p_inteligente:'Mais inteligente (divide com a RAM)',p_visao:'Imagens',p_codigo:'Código',p_embed:'Busca nas memórias',
      pd_rapido:'Folgado na placa de vídeo: respostas quase instantâneas.',pd_equilibrado:'O melhor modelo que roda 100% na GPU.',
      pd_inteligente:'Mais esperto, porém mais lento: parte roda na CPU/RAM.',pd_visao:'Entende imagens que você anexa.',pd_codigo:'Ajuda com programação.',
      pd_embed:'Modelo de embeddings para a busca por significado.',
      toolsYes:'Ferramentas',toolsNo:'Sem ferramentas',toolsHint:'Pode consultar e gravar memórias, grafo e projetos durante a conversa.',
      moe:'MoE · {a}B ativos',measured:'Medido (RTX 4050 Laptop 6 GB): {s} tok/s · {g}% GPU · ferramentas {f}',researched:'Pesquisado (não medido)',
      variant:'Quantização',instTag:'Instalado',recTag:'Recomendado',
      hwEdit:'Ajustar hardware',hwAuto:'detectado',hwManual:'informado à mão',hwCpu:'CPU: {c} ({n} núcleos)',hwGpus:'{n} GPUs somando {v} GB',
      hwUnified:'memória unificada ({v} GB para a GPU)',hwIgpu:'integrada (ignorada)',
      hwT:'Hardware deste PC',hwM:'Use se a detecção errar (ex.: GPU AMD/Intel, eGPU, várias placas). Deixe vazio para usar o detectado.',
      hwOn:'Usar os valores abaixo em vez da detecção',hwVram:'VRAM total (GB, 0 = sem GPU)',hwRamL:'RAM do sistema (GB)',hwGpuL:'Nome da GPU',hwUni:'Memória unificada (Mac Apple Silicon)',
      hwSaved:'Hardware salvo.',
      ferrT:'Ferramentas do modelo',ferrOn:'Deixar o modelo usar ferramentas',ferrOnD:'Modelos com suporte consultam memórias, grafo, projetos e conversas durante a resposta. Os outros continuam recebendo o contexto como antes.',
      ferrW:'Permitir gravar',ferrWD:'Salvar/editar/fixar memórias e criar conceitos e ligações no grafo. Tudo fica registrado e pode ser desfeito.',
      ferrRod:'Rodadas de ferramentas por resposta',ferrRodD:'Quantas vezes o modelo pode usar ferramentas antes de responder.',ferrLog:'Ver ações do modelo',
      profTools:'Ferramentas neste modelo',profToolsAuto:'automático (o que o Ollama informa)',profToolsOn:'sempre ligar',profToolsOff:'desligar'},
  en:{f_recomendados:'Recommended',f_ferramentas:'Tools',n_leve:'Light',n_moderado:'Moderate',n_pesado:'Heavy',n_grande:'Large',n_enorme:'Huge',
      fn_leve:'Light (≤4B)',fn_moderado:'Moderate (7–9B)',fn_pesado:'Heavy (12–14B)',fn_grande:'Large (20–35B, MoE)',fn_enorme:'Huge (70B+)',
      recHdr:'Recommended for your machine',recNone:'Could not detect your hardware. Enter it under “Adjust hardware”.',
      p_rapido:'Fastest',p_equilibrado:'Best that fits entirely',p_inteligente:'Smartest (splits with RAM)',p_visao:'Images',p_codigo:'Code',p_embed:'Memory search',
      pd_rapido:'Plenty of headroom on the GPU: near-instant replies.',pd_equilibrado:'The best model that runs 100% on the GPU.',
      pd_inteligente:'Smarter but slower: part of it runs on CPU/RAM.',pd_visao:'Understands images you attach.',pd_codigo:'Helps with programming.',
      pd_embed:'Embedding model for meaning-based search.',
      toolsYes:'Tools',toolsNo:'No tools',toolsHint:'Can look up and save memories, graph and projects during the chat.',
      moe:'MoE · {a}B active',measured:'Measured (RTX 4050 Laptop 6 GB): {s} tok/s · {g}% GPU · tools {f}',researched:'Researched (not measured)',
      variant:'Quantization',instTag:'Installed',recTag:'Recommended',
      hwEdit:'Adjust hardware',hwAuto:'detected',hwManual:'entered manually',hwCpu:'CPU: {c} ({n} cores)',hwGpus:'{n} GPUs totaling {v} GB',
      hwUnified:'unified memory ({v} GB for the GPU)',hwIgpu:'integrated (ignored)',
      hwT:'This machine’s hardware',hwM:'Use this if detection is wrong (e.g. AMD/Intel GPU, eGPU, several cards). Leave empty to use what was detected.',
      hwOn:'Use the values below instead of detection',hwVram:'Total VRAM (GB, 0 = no GPU)',hwRamL:'System RAM (GB)',hwGpuL:'GPU name',hwUni:'Unified memory (Apple Silicon Mac)',
      hwSaved:'Hardware saved.',
      ferrT:'Model tools',ferrOn:'Let the model use tools',ferrOnD:'Capable models look up memories, graph, projects and past chats while answering. Others keep receiving context as before.',
      ferrW:'Allow writing',ferrWD:'Save/edit/pin memories and create graph concepts and links. Everything is logged and can be undone.',
      ferrRod:'Tool rounds per answer',ferrRodD:'How many times the model may use tools before answering.',ferrLog:'View model actions',
      profTools:'Tools on this model',profToolsAuto:'automatic (as reported by Ollama)',profToolsOn:'always on',profToolsOff:'off'},
  es:{f_recomendados:'Recomendados',f_ferramentas:'Herramientas',n_leve:'Ligero',n_moderado:'Moderado',n_pesado:'Pesado',n_grande:'Grande',n_enorme:'Enorme',
      fn_leve:'Ligeros (≤4B)',fn_moderado:'Moderados (7–9B)',fn_pesado:'Pesados (12–14B)',fn_grande:'Grandes (20–35B, MoE)',fn_enorme:'Enormes (70B+)',
      recHdr:'Recomendados para tu equipo',recNone:'No se pudo detectar el hardware. Indícalo en “Ajustar hardware”.',
      p_rapido:'Más rápido',p_equilibrado:'El mejor que cabe entero',p_inteligente:'Más inteligente (divide con la RAM)',p_visao:'Imágenes',p_codigo:'Código',p_embed:'Búsqueda en memorias',
      pd_rapido:'Holgado en la GPU: respuestas casi instantáneas.',pd_equilibrado:'El mejor modelo que corre 100% en la GPU.',
      pd_inteligente:'Más listo pero más lento: una parte corre en CPU/RAM.',pd_visao:'Entiende imágenes adjuntas.',pd_codigo:'Ayuda con programación.',
      pd_embed:'Modelo de embeddings para la búsqueda por significado.',
      toolsYes:'Herramientas',toolsNo:'Sin herramientas',toolsHint:'Puede consultar y guardar memorias, grafo y proyectos durante la conversación.',
      moe:'MoE · {a}B activos',measured:'Medido (RTX 4050 Laptop 6 GB): {s} tok/s · {g}% GPU · herramientas {f}',researched:'Investigado (no medido)',
      variant:'Cuantización',instTag:'Instalado',recTag:'Recomendado',
      hwEdit:'Ajustar hardware',hwAuto:'detectado',hwManual:'indicado a mano',hwCpu:'CPU: {c} ({n} núcleos)',hwGpus:'{n} GPUs sumando {v} GB',
      hwUnified:'memoria unificada ({v} GB para la GPU)',hwIgpu:'integrada (ignorada)',
      hwT:'Hardware de este equipo',hwM:'Úsalo si la detección falla (p. ej. GPU AMD/Intel, eGPU, varias tarjetas). Déjalo vacío para usar lo detectado.',
      hwOn:'Usar estos valores en lugar de la detección',hwVram:'VRAM total (GB, 0 = sin GPU)',hwRamL:'RAM del sistema (GB)',hwGpuL:'Nombre de la GPU',hwUni:'Memoria unificada (Mac Apple Silicon)',
      hwSaved:'Hardware guardado.',
      ferrT:'Herramientas del modelo',ferrOn:'Dejar que el modelo use herramientas',ferrOnD:'Los modelos compatibles consultan memorias, grafo, proyectos y chats anteriores al responder. Los demás siguen recibiendo el contexto como antes.',
      ferrW:'Permitir escribir',ferrWD:'Guardar/editar/fijar memorias y crear conceptos y vínculos en el grafo. Todo queda registrado y se puede deshacer.',
      ferrRod:'Rondas de herramientas por respuesta',ferrRodD:'Cuántas veces puede usar herramientas antes de responder.',ferrLog:'Ver acciones del modelo',
      profTools:'Herramientas en este modelo',profToolsAuto:'automático (lo que informa Ollama)',profToolsOn:'siempre activar',profToolsOff:'desactivar'}
};
if(window.AjustesUI&&AjustesUI._D){Object.keys(S).forEach(function(l){AjustesUI._D[l]=AjustesUI._D[l]||{};
  Object.keys(S[l]).forEach(function(k){if(!(k in AjustesUI._D[l]))AjustesUI._D[l][k]=S[l][k];});});}
function t(k,v){return window.AjustesUI?AjustesUI.t(k,v):k;}
function el(tag,attrs,kids){var e=document.createElement(tag);
  if(attrs)Object.keys(attrs).forEach(function(k){var v=attrs[k];if(v==null)return;
    if(k==='class')e.className=v;else if(k==='text')e.textContent=v;else if(k.slice(0,2)==='on')e[k]=v;else if(k==='value'||k==='selected')e[k]=v;else e.setAttribute(k,v);});
  (kids||[]).forEach(function(c){if(c!=null)e.append(c);});return e;}
function gb(n){return (n>=10?Math.round(n):Math.round(n*10)/10)+' GB';}
function ico(n,sz){return (window.AtlasIcons&&AtlasIcons.el(n,sz||13))||null;}
function chip(cls,n,txt,title){var s=el('span',{class:cls,title:title||null});var i=ico(n,12);if(i)s.append(i);s.append(el('span',{text:txt}));return s;}
function ibtn(txt,n,cls){var b=el('button',{type:'button',class:cls||'baixar',text:txt});if(window.AtlasIcons)AtlasIcons.label(b,n,txt,15);return b;}
var PAPEL_ICO={rapido:'zap',equilibrado:'check-circle',inteligente:'brain',visao:'eye',codigo:'code',embed:'search'};

var FILTROS=['recomendados','todos','leve','moderado','pesado','grande','enorme','ferramentas','visao','codigo','embed'];
var ESCOLHA={};                                      // modelo -> tag de quantização escolhida no cartão
function rotuloFiltro(f){return ['leve','moderado','pesado','grande','enorme'].indexOf(f)>=0?t('fn_'+f):t('f_'+f);}
function filtrar(lista,f){
  if(f==='todos'||f==='recomendados')return lista;
  if(f==='ferramentas')return lista.filter(function(m){return m.ferramentas;});
  if(['leve','moderado','pesado','grande','enorme'].indexOf(f)>=0)return lista.filter(function(m){return m.nivel===f;});
  return lista.filter(function(m){return (m.usos||[]).indexOf(f)>=0;});
}
function tag(m){return ESCOLHA[m.nome]||m.sugerida||m.nome;}

// selo de encaixe para uma variante (reaproveita as classes .fit do painel)
function selo(cabe,vram){
  if(!cabe)return null;
  var txt=cabe==='vram'?t('fitVram',{v:Math.round((vram||0)*10)/10}):cabe==='parcial'?(vram?t('fitParcial'):t('fitCpu')):t('fitGrande');
  return el('span',{class:'fit var '+cabe,text:txt,title:t('fitNote')});
}

// extras de um cartão de modelo (catálogo ou instalado)
function extras(div,m,e,instalado){
  if(!m)return;
  var tags=div.querySelector('.tags')||div.appendChild(el('div',{class:'tags'}));
  if(m.nivel){var nv=t('n_'+m.nivel);                    // evita "Leve" duplicado (uso + nível)
    [].slice.call(tags.querySelectorAll('.tag')).forEach(function(x){if(x.textContent===nv)x.remove();});
    tags.append(chip('tag lvl lvl-'+m.nivel,'gauge',nv));}
  if(m.tipo!=='embed'){
    var tl=instalado&&instalado.ferramentas!=null?instalado.ferramentas:m.ferramentas;
    tags.append(chip('tag'+(tl?' pin':' off'),tl?'wrench':'ban',tl?t('toolsYes'):t('toolsNo'),tl?t('toolsHint'):''));
  }
  if(m.ativos)tags.append(chip('tag','layers',t('moe',{a:m.ativos})));
  if(m.recomendado&&m.recomendado.length)tags.append(chip('tag why','star',t('recTag')+': '+m.recomendado.map(function(p){return t('p_'+p);}).join(', ')));
  var L=(e&&e.config&&e.config.idioma)||'pt';
  if(m.fortes&&(m.fortes[L]||m.fortes.pt)){
    var d=div.querySelector('.desc');var txt=m.fortes[L]||m.fortes.pt;
    if(d)d.textContent=txt;else div.append(el('div',{class:'desc',text:txt}));
  }
  if(m.tipo!=='embed'&&m.nivel){
    var md=m.medido;
    div.append(el('div',{class:'d mmed'+(md?' ok':'')},[ico(md?'gauge':'info',13),el('span',{text:md?t('measured',{s:md.tok_s,g:md.gpu_pct,f:md.ferramentas||'—'}):t('researched')})]));
  }
  // variantes de quantização (só no catálogo, antes de baixar)
  if(!instalado&&m.variantes&&m.variantes.length>1){
    var hw=(e&&e.hardware)||{};
    var sel=el('select',{class:'sel','aria-label':t('variant')});
    m.variantes.forEach(function(v){
      var marca=v.cabe==='vram'?'✓':v.cabe==='parcial'?'≈':v.cabe==='grande'?'✕':'';
      sel.append(el('option',{value:v.tag,text:(v.quant||'padrão')+' · '+gb(v.gb)+' '+marca+' — '+v.tag}));
    });
    sel.value=tag(m);
    var slot=el('div',{class:'fitslot'});
    function atualizar(){ESCOLHA[m.nome]=sel.value;var v=m.variantes.find(function(x){return x.tag===sel.value;});
      slot.textContent='';var s=v&&selo(v.cabe,hw.vram_gb);if(s)slot.append(s);
      var velho=div.querySelector('.fit:not(.var)');if(velho)velho.style.display='none';}
    sel.onchange=atualizar;
    div.append(el('div',{class:'pullrow var'},[el('label',{class:'d',text:t('variant')}),sel]),slot);
    atualizar();
  }
}

// visão "Recomendados para o seu PC"
function recomendados(box,e,cartao,cartaoInstalado){
  var recs=e.recomendacoes||{};
  var papeis=['rapido','equilibrado','inteligente','visao','codigo','embed'].filter(function(p){return recs[p];});
  box.append(el('h3',{class:'sub rec'},[ico('star',13),el('span',{text:t('recHdr')})]));
  if(!papeis.length){box.append(el('div',{class:'ui-empty',text:t('recNone')}));return;}
  var vistos={};
  papeis.forEach(function(p){
    var r=recs[p];
    var m=(e.catalogo||[]).find(function(x){return x.nome===r.nome;});if(!m)return;
    box.append(el('div',{class:'recrole'},[el('span',{class:'ri'},[ico(PAPEL_ICO[p]||'star',15)]),
      el('div',{class:'rt'},[el('div',{class:'nm',text:t('p_'+p)}),el('div',{class:'d',text:t('pd_'+p)})])]));
    if(vistos[r.nome]){box.append(el('div',{class:'d recsame'},[ico('arrow-up',13),el('span',{text:m.rotulo})]));return;}
    vistos[r.nome]=1;ESCOLHA[m.nome]=ESCOLHA[m.nome]||r.tag;
    var inst=(e.instalados_info||[]).find(function(i){return i.nome===r.tag||i.nome===r.nome||i.nome===r.nome+':latest'||i.catalogo===r.nome;});
    box.append(inst?cartaoInstalado(inst,e,e.config):cartao(m,e,e.config));
  });
}

// linha de hardware com GPUs, CPU e ajuste manual
function hardware(container,e,H){
  var hw=e.hardware||{};
  var linhas=[];
  var uteis=(hw.gpus||[]).filter(function(g){return !g.integrada;});
  if(hw.unificada)linhas.push((hw.gpu||'Apple Silicon')+' · '+t('hwUnified',{v:hw.vram_gb}));
  else if(uteis.length>1)linhas.push(t('hwGpus',{n:uteis.length,v:hw.vram_gb})+': '+uteis.map(function(g){return g.nome+' ('+g.vram_gb+' GB)';}).join(', '));
  (hw.gpus||[]).filter(function(g){return g.integrada;}).forEach(function(g){linhas.push(g.nome+' · '+t('hwIgpu'));});
  if(hw.cpu)linhas.push(t('hwCpu',{c:hw.cpu,n:hw.nucleos||hw.threads||'?'}));
  linhas.push(hw.fonte==='manual'?t('hwManual'):t('hwAuto'));
  var d=el('div',{class:'d',text:linhas.join(' · ')});
  var b=ibtn(t('hwEdit'),'sliders','baixar');
  b.onclick=async function(){
    var man=(e.config&&e.config.hardware_manual)||{};var det=e.hardware_detectado||hw;
    var r=await AtlasUI.form({title:t('hwT'),message:t('hwM'),okText:t('p_projSave'),
      fields:[{name:'ativo',type:'checkbox',label:t('hwOn'),value:!!man.ativo},
              {name:'vram_gb',type:'number',label:t('hwVram'),min:0,max:1024,step:0.5,value:man.vram_gb,placeholder:String(det.vram_gb||0)},
              {name:'ram_gb',type:'number',label:t('hwRamL'),min:0,max:4096,step:1,value:man.ram_gb,placeholder:String(det.ram_gb||0)},
              {name:'gpu',type:'text',label:t('hwGpuL'),value:man.gpu||'',placeholder:det.gpu||''},
              {name:'unificada',type:'checkbox',label:t('hwUni'),value:!!man.unificada}]});
    if(!r)return;
    await H.salvar({hardware_manual:{ativo:!!r.ativo,vram_gb:r.vram_gb==null?null:r.vram_gb,ram_gb:r.ram_gb==null?null:r.ram_gb,gpu:r.gpu||'',unificada:!!r.unificada}});
    AtlasUI.toast(t('hwSaved'),{kind:'ok'});
  };
  container.append(el('div',{class:'hw hw2'},[d,b]));
}

// seção de ferramentas (dentro de "Memória e contexto")
function secaoFerramentas(box,H,c,A){
  var f=(c&&c.ferramentas)||{};
  box.append(el('h3',{class:'sub rec'},[ico('wrench',13),el('span',{text:t('ferrT')})]));
  box.append(A.linha(t('ferrOn'),t('ferrOnD'),A.interruptor(f.ativo!==false,function(v){H.salvar({ferramentas:{ativo:v}});},t('ferrOn'))));
  box.append(A.linha(t('ferrW'),t('ferrWD'),A.interruptor(f.escrita!==false,function(v){H.salvar({ferramentas:{escrita:v}});},t('ferrW'))));
  box.append(A.linha(t('ferrRod'),t('ferrRodD'),A.numero({label:t('ferrRod'),min:1,max:8,int:true,step:1,value:f.max_rodadas||4,
    salvar:function(v){H.salvar({ferramentas:{max_rodadas:v}});}})));
  var b=ibtn(t('ferrLog'),'file-text','baixar');b.onclick=function(){if(window.AtlasTools)AtlasTools.registro();};
  box.append(el('div',{class:'sfoot'},[b]));
}

// campo extra do perfil do modelo: ferramentas auto/ligar/desligar
function campoPerfil(p){
  var v=p.ferramentas===true?'on':p.ferramentas===false?'off':'auto';
  return {name:'ferramentas',type:'select',label:t('profTools'),value:v,
          options:[{v:'auto',l:t('profToolsAuto')},{v:'on',l:t('profToolsOn')},{v:'off',l:t('profToolsOff')}]};
}
function valorPerfil(v){return v==='on'?true:v==='off'?false:null;}

window.AtlasModelos={FILTROS:FILTROS,rotuloFiltro:rotuloFiltro,filtrar:filtrar,tag:tag,extras:extras,recomendados:recomendados,
  hardware:hardware,secaoFerramentas:secaoFerramentas,campoPerfil:campoPerfil,valorPerfil:valorPerfil};
})();
