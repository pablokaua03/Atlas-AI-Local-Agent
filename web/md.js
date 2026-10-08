/* Atlas — markdown seguro para as respostas do modelo.
   Todo texto é escapado ANTES de qualquer formatação; só geramos um conjunto fixo de tags.
   Links só com http(s)/mailto. Bloco de código sem fechamento (resposta ainda chegando) vira código.
   AtlasMD.render(texto) → string HTML · AtlasMD.labels({copy, copied}) · AtlasMD.ligarCopiar(raiz) */
(function(){
'use strict';
var L={copy:'copiar',copied:'copiado'};
var P0='\uE000',P1='\uE001';

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}

function urlOk(u){return /^(https?:\/\/|mailto:)[^\s<>"']+$/i.test(u);}

// ── inline: código, links, ênfase ──
function inline(src){
  var guard=[];
  function guarda(html){guard.push(html);return P0+(guard.length-1)+P1;}
  var s=src.replace(/`([^`\n]+)`/g,function(_m,c){return guarda('<code>'+esc(c)+'</code>');});
  s=s.replace(/\[([^\]\n]{1,300})\]\(([^)\s]{1,2000})\)/g,function(m,txt,url){
    if(!urlOk(url))return m;
    return guarda('<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+inlineSimples(txt)+'</a>');});
  s=s.replace(/(^|[\s(])((?:https?:\/\/)[^\s<>"'`)\]]{2,2000})/g,function(_m,pre,url){
    var fim='';var mm=url.match(/[.,;:!?]+$/);if(mm){fim=mm[0];url=url.slice(0,-fim.length);}
    return pre+guarda('<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(url)+'</a>')+fim;});
  s=inlineSimples(s);
  return s.replace(new RegExp(P0+'(\\d+)'+P1,'g'),function(_m,i){return guard[+i]!==undefined?guard[+i]:'';});
}
function inlineSimples(s){
  s=esc(s);
  s=s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g,'<strong>$1</strong>').replace(/__(?=\S)([\s\S]*?\S)__/g,'<strong>$1</strong>');
  s=s.replace(/(^|[^*\w])\*(?=\S)([^*\n]*?\S)\*(?!\*)/g,'$1<em>$2</em>').replace(/(^|[^_\w])_(?=\S)([^_\n]*?\S)_(?![_\w])/g,'$1<em>$2</em>');
  s=s.replace(/~~(?=\S)([^~\n]*?\S)~~/g,'<del>$1</del>');
  return s;
}

// ── blocos ──
var RE_FENCE=/^\s{0,3}(`{3,}|~{3,})\s*([\w+#.\-]*)\s*$/;
var RE_HEAD=/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
var RE_HR=/^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
var RE_QUOTE=/^\s{0,3}>\s?(.*)$/;
var RE_ITEM=/^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
var RE_SEP=/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

function celulas(l){
  var s=l.trim();if(s.charAt(0)==='|')s=s.slice(1);if(s.slice(-1)==='|'&&s.slice(-2)!=='\\|')s=s.slice(0,-1);
  return s.split(/(?<!\\)\|/).map(function(c){return c.trim().replace(/\\\|/g,'|');});
}
function tabela(linhas){
  var cab=celulas(linhas[0]),al=celulas(linhas[1]).map(function(c){
    return /^:-+:$/.test(c)?'center':/^-+:$/.test(c)?'right':/^:-+$/.test(c)?'left':'';});
  function td(tag,txt,i){var a=al[i]?' style="text-align:'+al[i]+'"':'';return '<'+tag+a+'>'+inline(txt)+'</'+tag+'>';}
  var h='<div class="md-table"><table><thead><tr>'+cab.map(function(c,i){return td('th',c,i);}).join('')+'</tr></thead><tbody>';
  for(var k=2;k<linhas.length;k++){var cs=celulas(linhas[k]);
    h+='<tr>'+cab.map(function(_c,i){return td('td',cs[i]||'',i);}).join('')+'</tr>';}
  return h+'</tbody></table></div>';
}
function codigo(lang,linhas,aberto){
  var l=esc((lang||'').slice(0,20));
  return '<div class="md-code'+(aberto?' open':'')+'"><div class="md-codebar"><span>'+(l||'texto')+'</span>'+
    '<button type="button" class="md-copy" data-md-copy>'+esc(L.copy)+'</button></div><pre><code'+(l?' data-lang="'+l+'"':'')+'>'+
    esc(linhas.join('\n'))+'</code></pre></div>';
}
function indent(s){var m=s.match(/^\s*/)[0];return m.replace(/\t/g,'    ').length;}

function lista(linhas){
  // linhas: itens e continuações; aninha pelo recuo
  var base=indent(linhas[0]),m0=linhas[0].match(RE_ITEM),ord=/\d/.test(m0[2]);
  var ini=ord?parseInt(m0[2],10):1;
  var itens=[],cur=null;
  linhas.forEach(function(l){
    var m=l.match(RE_ITEM);
    if(m&&indent(l)<=base+1){cur={txt:[m[3]],sub:[]};itens.push(cur);}
    else if(cur){if(m||indent(l)>base)cur.sub.push(l);else cur.txt.push(l.trim());}
  });
  var tag=ord?'ol':'ul';
  var h='<'+tag+(ord&&ini!==1?' start="'+ini+'"':'')+'>';
  itens.forEach(function(it){
    var txt=it.txt.join(' '),tarefa='';
    var tm=txt.match(/^\[([ xX])\]\s+(.*)$/);
    if(tm){tarefa='<input type="checkbox" disabled'+(tm[1]!==' '?' checked':'')+'> ';txt=tm[2];}
    var sub='';
    if(it.sub.length){
      var subItens=it.sub.filter(function(l){return l.trim();});
      sub=subItens.length&&RE_ITEM.test(subItens[0])?lista(subItens):'<p>'+inline(subItens.map(function(x){return x.trim();}).join(' '))+'</p>';
    }
    h+='<li>'+tarefa+inline(txt)+sub+'</li>';
  });
  return h+'</'+tag+'>';
}

function blocos(linhas,prof){
  var out=[],i=0,n=linhas.length,par=[];
  function fechaPar(){if(par.length){out.push('<p>'+par.map(inline).join('<br>')+'</p>');par=[];}}
  while(i<n){
    var l=linhas[i],m;
    if((m=l.match(RE_FENCE))){
      fechaPar();var marca=m[1],lang=m[2],cod=[];i++;
      while(i<n&&!(new RegExp('^\\s{0,3}'+marca.charAt(0)+'{'+marca.length+',}\\s*$').test(linhas[i]))){cod.push(linhas[i]);i++;}
      var aberto=i>=n;i++;
      out.push(codigo(lang,cod,aberto));continue;
    }
    if(!l.trim()){fechaPar();i++;continue;}
    if((m=l.match(RE_HEAD))){fechaPar();var nv=Math.min(6,m[1].length+2);out.push('<h'+nv+'>'+inline(m[2])+'</h'+nv+'>');i++;continue;}
    if(RE_HR.test(l)){fechaPar();out.push('<hr>');i++;continue;}
    if(RE_QUOTE.test(l)&&prof<4){
      fechaPar();var q=[];
      while(i<n&&(m=linhas[i].match(RE_QUOTE))){q.push(m[1]);i++;}
      out.push('<blockquote>'+blocos(q,prof+1)+'</blockquote>');continue;
    }
    if(l.indexOf('|')>=0&&i+1<n&&RE_SEP.test(linhas[i+1])&&linhas[i+1].indexOf('-')>=0){
      fechaPar();var tb=[l,linhas[i+1]];i+=2;
      while(i<n&&linhas[i].trim()&&linhas[i].indexOf('|')>=0){tb.push(linhas[i]);i++;}
      out.push(tabela(tb));continue;
    }
    if(RE_ITEM.test(l)){
      fechaPar();var li=[l];i++;
      var ordem=/\d/.test(l.match(RE_ITEM)[2]);
      var mesmoTipo=function(x){var mm=x.match(RE_ITEM);return mm&&(indent(x)>indent(l)+1||/\d/.test(mm[2])===ordem);};
      while(i<n){
        var x=linhas[i];
        if(RE_FENCE.test(x))break;
        if(!x.trim()){ // linha em branco: a lista continua se o próximo for item do mesmo tipo ou recuado
          var prox=linhas[i+1];
          if(prox!==undefined&&prox.trim()&&(mesmoTipo(prox)||(!RE_ITEM.test(prox)&&indent(prox)>indent(l)))){i++;continue;}
          break;
        }
        if(RE_ITEM.test(x)&&!mesmoTipo(x))break;
        if(RE_ITEM.test(x)||indent(x)>indent(l)){li.push(x);i++;continue;}
        if(RE_HEAD.test(x)||RE_QUOTE.test(x))break;
        li.push(x);i++;   // continuação preguiçosa do item
      }
      out.push(lista(li));continue;
    }
    par.push(l);i++;
  }
  fechaPar();
  return out.join('');
}

function render(texto){
  var s=String(texto==null?'':texto).replace(/\r\n?/g,'\n').replace(/[\uE000\uE001]/g,'');
  return blocos(s.split('\n'),0);
}

function copiarTexto(txt){
  if(navigator.clipboard&&window.isSecureContext!==false)return navigator.clipboard.writeText(txt);
  return new Promise(function(ok,falha){
    var ta=document.createElement('textarea');ta.value=txt;ta.style.position='fixed';ta.style.opacity='0';
    document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy');ok();}catch(e){falha(e);}finally{ta.remove();}
  });
}

// delegação: um único listener cuida de todos os botões "copiar" dos blocos de código
function ligarCopiar(raiz){
  (raiz||document).addEventListener('click',function(e){
    var b=e.target.closest&&e.target.closest('[data-md-copy]');if(!b)return;
    var box=b.closest('.md-code'),code=box&&box.querySelector('code');if(!code)return;
    copiarTexto(code.textContent).then(function(){
      b.textContent=L.copied;b.classList.add('ok');
      setTimeout(function(){b.textContent=L.copy;b.classList.remove('ok');},1400);
    });
  });
}

window.AtlasMD={render:render,esc:esc,labels:function(o){for(var k in o)if(o[k])L[k]=o[k];},ligarCopiar:ligarCopiar,copiar:copiarTexto};
})();
