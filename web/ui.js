/* Atlas UI — modais, toasts e navegação inferior compartilhados (sem dependências).
   AtlasUI.confirm({title,message,okText,cancelText,danger}) -> Promise<boolean>
   AtlasUI.form({title,message,fields:[{name,label,value,type:'text'|'textarea'|'checkbox'|'select'|'number',options:[{v,l}],min,max,step,hint,placeholder,required}],okText,cancelText,danger})
       -> Promise<{[name]:valor} | null>
   AtlasUI.modal({title,message,build:function(box,fechar){...}}) -> Promise (conteúdo livre; fechar(valor) resolve)
   AtlasUI.prompt({title,label,value,...}) -> Promise<string|null>
   AtlasUI.toast(msg,{kind:'err'|'ok'|'info',ms})
   AtlasUI.nav(ativo,{idioma,onConfig}) monta o menu inferior (celular) */
(function(){
  'use strict';
  var L={ok:'Confirmar',cancel:'Cancelar',close:'Fechar',required:'Preencha este campo.',range:'Valor entre {min} e {max}.'};
  function el(tag,props,kids){var e=document.createElement(tag);if(props)Object.keys(props).forEach(function(k){
    if(k==='class')e.className=props[k];else if(k==='text')e.textContent=props[k];else e[k]=props[k];});
    (kids||[]).forEach(function(c){if(c)e.append(c);});return e;}
  var seq=0;

  function abrir(build){
    return new Promise(function(resolve){
      var prev=document.activeElement,id='ui-dlg-'+(++seq);
      var back=el('div',{class:'ui-back'});
      var box=el('div',{class:'ui-modal',role:'dialog'});
      box.setAttribute('aria-modal','true');box.setAttribute('aria-labelledby',id);
      back.append(box);
      var fechado=false;
      function fechar(v){if(fechado)return;fechado=true;document.removeEventListener('keydown',tecla,true);back.remove();
        if(prev&&prev.focus&&document.contains(prev))prev.focus();resolve(v);}
      function tecla(e){
        if(e.key==='Escape'){e.preventDefault();e.stopPropagation();fechar(null);return;}
        if(e.key==='Tab'){var f=[].slice.call(box.querySelectorAll('button,input,textarea,select,[tabindex]:not([tabindex="-1"])')).filter(function(x){return !x.disabled;});
          if(!f.length)return;var a=f[0],z=f[f.length-1];
          if(e.shiftKey&&document.activeElement===a){e.preventDefault();z.focus();}
          else if(!e.shiftKey&&document.activeElement===z){e.preventDefault();a.focus();}}
      }
      document.addEventListener('keydown',tecla,true);
      back.addEventListener('mousedown',function(e){if(e.target===back)fechar(null);});
      build(box,id,fechar);
      document.body.append(back);
    });
  }
  function cabeca(box,id,title,message){
    box.append(el('h2',{id:id,text:title||''}));
    if(message)box.append(el('p',{text:message}));
  }

  function confirmar(o){
    o=o||{};
    return abrir(function(box,id,fechar){
      cabeca(box,id,o.title||'Confirmar',o.message);
      var cancel=el('button',{class:'ui-btn',type:'button',text:o.cancelText||L.cancel});
      var ok=el('button',{class:'ui-btn '+(o.danger?'danger':'pri'),type:'button',text:o.okText||L.ok});
      cancel.onclick=function(){fechar(false);};ok.onclick=function(){fechar(true);};
      box.append(el('div',{class:'ui-actions'},[cancel,ok]));
      // ação destrutiva: o foco inicial fica em "Cancelar" (Enter não apaga sem querer)
      setTimeout(function(){(o.danger?cancel:ok).focus();},20);
    }).then(function(v){return v===true;});
  }

  function formulario(o){
    o=o||{};var campos=o.fields||[];
    return abrir(function(box,id,fechar){
      cabeca(box,id,o.title,o.message);
      var form=el('form',{noValidate:true}),inputs={};
      campos.forEach(function(c){
        var tipo=c.type||'text',inp;
        var lab=el('label',{class:'ui-field'+(tipo==='checkbox'?' chk':'')});
        if(tipo==='checkbox'){inp=el('input',{type:'checkbox',checked:!!c.value});lab.append(inp,el('span',{text:c.label||''}));}
        else if(tipo==='select'){
          inp=el('select',{});(c.options||[]).forEach(function(op){var v=typeof op==='object'?op.v:op,l=typeof op==='object'?op.l:op;
            var o2=el('option',{value:v,text:l});if(String(v)===String(c.value))o2.selected=true;inp.append(o2);});
          lab.append(el('span',{text:c.label||''}),inp);
        }
        else{
          if(tipo==='number'){inp=el('input',{type:'number',value:(c.value==null?'':c.value)});
            if(c.min!=null)inp.min=c.min;if(c.max!=null)inp.max=c.max;inp.step=c.step!=null?c.step:'any';}
          else inp=tipo==='textarea'?el('textarea',{value:c.value||''}):el('input',{type:'text',value:c.value||''});
          if(c.placeholder)inp.placeholder=c.placeholder;
          if(c.maxLength)inp.maxLength=c.maxLength;
          lab.append(el('span',{text:c.label||''}),inp);
        }
        if(c.hint)lab.append(el('small',{class:'ui-hint',text:c.hint}));
        inputs[c.name]=inp;form.append(lab);
      });
      var err=el('div',{class:'ui-err',role:'alert'});form.append(err);
      var cancel=el('button',{class:'ui-btn',type:'button',text:o.cancelText||L.cancel});
      var ok=el('button',{class:'ui-btn '+(o.danger?'danger':'pri'),type:'submit',text:o.okText||L.ok});
      cancel.onclick=function(){fechar(null);};
      form.append(el('div',{class:'ui-actions'},[cancel,ok]));
      form.onsubmit=function(e){e.preventDefault();var out={};
        for(var i=0;i<campos.length;i++){var c=campos[i],inp=inputs[c.name];
          var v=(c.type==='checkbox')?inp.checked:inp.value.trim();
          if(c.required&&v===''){err.textContent=L.required;inp.focus();return;}
          if(c.type==='number'){
            if(v===''){v=null;}
            else{var n=Number(v);
              if(!isFinite(n)||(c.min!=null&&n<c.min)||(c.max!=null&&n>c.max)){
                err.textContent=L.range.replace('{min}',c.min).replace('{max}',c.max);inp.focus();return;}
              v=n;}}
          out[c.name]=v;}
        fechar(out);};
      // Enter em textarea quebra linha; Ctrl+Enter envia
      form.addEventListener('keydown',function(e){if(e.key==='Enter'&&e.ctrlKey)form.requestSubmit?form.requestSubmit():form.onsubmit(e);});
      box.append(form);
      var primeiro=campos.length?inputs[campos[0].name]:ok;
      setTimeout(function(){primeiro.focus();if(primeiro.select&&primeiro.type==='text')primeiro.select();},20);
    });
  }

  // modal de conteúdo livre: build(box, fechar) preenche o corpo; devolve o valor passado a fechar()
  function modal(o){
    o=o||{};
    return abrir(function(box,id,fechar){cabeca(box,id,o.title,o.message);if(o.wide)box.classList.add('wide');o.build(box,fechar);});
  }

  function prompt_(o){
    o=o||{};
    return formulario({title:o.title,message:o.message,okText:o.okText,cancelText:o.cancelText,danger:o.danger,
      fields:[{name:'v',label:o.label||'',value:o.value||'',placeholder:o.placeholder,required:o.required!==false,maxLength:o.maxLength}]})
      .then(function(r){return r?r.v:null;});
  }

  function toast(msg,o){
    o=o||{};var host=document.getElementById('ui-toasts');
    if(!host){host=el('div',{id:'ui-toasts'});host.setAttribute('aria-live','polite');document.body.append(host);}
    var t=el('div',{class:'ui-toast'+(o.kind==='err'?' err':o.kind==='ok'?' ok':''),text:String(msg||''),role:o.kind==='err'?'alert':'status'});
    host.append(t);setTimeout(function(){t.remove();},o.ms||(o.kind==='err'?6000:3200));
  }

  var NAV={pt:{chat:'Chat',mem:'Memórias',graph:'Grafo',cfg:'Ajustes'},en:{chat:'Chat',mem:'Memories',graph:'Graph',cfg:'Settings'},
           es:{chat:'Chat',mem:'Memorias',graph:'Grafo',cfg:'Ajustes'}};
  function nav(ativo,o){
    o=o||{};var old=document.querySelector('.ui-bnav');if(old)old.remove();
    var tx=NAV[o.idioma]||NAV.pt,n=el('nav',{class:'ui-bnav'});n.setAttribute('aria-label','Principal');
    function ic(nome){var s=el('span',{class:'ic'});s.setAttribute('aria-hidden','true');
      if(window.AtlasIcons)s.innerHTML=AtlasIcons.svg(nome,20);return s;}
    [['chat','/','message'],['mem','/memorias','folder'],['graph','/grafo','graph']].forEach(function(x){
      var a=el('a',{href:x[1],class:ativo===x[0]?'on':''},[ic(x[2]),el('span',{text:tx[x[0]]})]);
      if(ativo===x[0])a.setAttribute('aria-current','page');n.append(a);});
    if(o.onConfig){var b=el('button',{type:'button'},[ic('settings'),el('span',{text:tx.cfg})]);b.onclick=o.onConfig;n.append(b);}
    document.body.append(n);document.body.classList.add('ui-has-nav');
  }

  window.AtlasUI={confirm:confirmar,form:formulario,modal:modal,el:el,prompt:prompt_,toast:toast,nav:nav,
    labels:function(o){Object.keys(o||{}).forEach(function(k){L[k]=o[k];});}};
})();
