/* Atlas — painel de Ajustes (seções), catálogo de modelos e controles de contexto do chat.
   Depende de ui.js (AtlasUI). Tudo é montado com textContent (nada de HTML vindo de dados).
   AjustesUI.init(host)  host = { estado(), salvar(patch), conf(msg,opts), baixar(nome,btn,pr), excluir(nome),
                                  chat(), seg(sel,vals,cur,lab,cb), recarregar() }
   AjustesUI.setLang(l) · AjustesUI.titulos() · AjustesUI.render() · AjustesUI.chat.* */
(function(){
'use strict';
var LANG='pt',H=null,PROJ=[],FILTRO='recomendados',TUDO=false;

var D={
pt:{
 s_modelo:'Modelo',s_memoria:'Memória e contexto',s_personalidade:'Personalidade e instruções',s_geracao:'Geração',
 s_privacidade:'Privacidade e segurança',s_geral:'Idioma e tema',s_backup:'Backup e arquivos',
 s_modelo_d:'Escolha o modelo, veja o que cabe no seu PC e ajuste a janela.',
 s_memoria_d:'Como o Atlas escolhe o que o modelo lembra em cada resposta.',
 s_personalidade_d:'Tom e regras que valem para todas as respostas.',
 s_geracao_d:'Parâmetros de geração do texto.',
 s_privacidade_d:'Controle de pausa, cofre e API.',s_geral_d:'Idioma, tema e como o agente te chama.',s_backup_d:'Exportar e restaurar seus dados.',
 restaurar:'Restaurar padrão',restConf:'Restaurar os valores padrão desta seção? Seus dados (memórias, conversas) não são apagados.',restOk:'Seção restaurada.',
 saved:'Salvo.',clamped:'Valor ajustado para o intervalo permitido ({min}–{max}).',invalid:'Valor inválido.',range:'{min}–{max}',auto:'auto',
 f_todos:'Todos',f_geral:'Geral',f_codigo:'Código',f_raciocinio:'Raciocínio',f_visao:'Visão',f_leve:'Leves (PC fraco)',f_embed:'Embeddings',
 u_geral:'Uso geral',u_codigo:'Código',u_raciocinio:'Raciocínio',u_visao:'Visão',u_leve:'Leve',u_embed:'Embeddings',
 d_geral:'Conversa e tarefas do dia a dia.',d_codigo:'Ajuda a escrever e revisar código.',d_raciocinio:'Pensa passo a passo em problemas difíceis (mais lento).',
 d_visao:'Entende imagens que você anexa.',d_leve:'Roda bem em PCs com pouca memória.',d_embed:'Usado na busca por significado (memórias e documentos). Não conversa.',
 size:'Download',ram:'RAM ≈',ctxw:'Janela',installed:'Instalado',notInst:'Não instalado',active:'Em uso',use:'Usar',useEmbed:'Usar na busca',
 dl:'Baixar',dling:'Baixando…',retry:'Tentar de novo',del:'Excluir',profile:'Perfil',heavy:'Pode ficar pesado neste PC ({ram} GB de RAM)',
 ollamaOff:'Ligue o Ollama para baixar modelos.',dlErr:'Não foi possível baixar {m}: {e}',dlNoOllama:'O Ollama não respondeu. Inicie-o e tente de novo.',
 dlDone:'{m} instalado.',dlOffline:'Sem conexão para baixar o modelo. Verifique a internet.',dlDisk:'Sem espaço em disco para este modelo.',
 pcRam:'Memória deste PC: {ram} GB. Valores de RAM e tamanho são aproximados.',
 prov:'Provedores',provLocal:'local',provSoon:'Provedores externos podem ser adicionados aqui (nenhum configurado; nenhuma chave é enviada).',
 numctx:'Janela de contexto padrão',numctx_d:'Tokens que o modelo lê por vez. Maior = lembra mais, usa mais RAM. Cada modelo tem um teto.',
 profTitle:'Perfil do modelo',profMsg:'Vale só para {m}. Deixe vazio para usar o padrão.',profCtx:'Janela de contexto (tokens)',profTemp:'Temperatura',profTopP:'Top-p',profMax:'Máx. de tokens da resposta',
 profCtxHint:'1024 – {max}',profSaved:'Perfil salvo.',profReset:'Perfil restaurado.',
 c_pct:'Parte da janela para contexto (%)',c_pct_d:'Quanto da janela do modelo pode ser ocupado por memórias, grafo e documentos.',
 c_max:'Máx. de memórias por resposta',c_max_d:'Quantas memórias entram no contexto. 0 desliga as memórias.',
 c_rec:'Meia-vida da recência (dias)',c_rec_d:'Memórias mais antigas pesam menos no ranking (metade a cada N dias).',
 c_hist:'Mensagens recentes do chat',c_hist_d:'Quantas mensagens anteriores desta conversa o modelo vê.',
 c_conv:'Lembrar de outras conversas',c_conv_d:'Busca trechos de conversas anteriores quando relevante.',
 c_fatos:'Salvar fatos duráveis',c_fatos_d:'Quando o Atlas encontra algo durável sobre você. Senhas, tokens e cartões nunca são salvos.',
 fm_perguntar:'Perguntar',fm_automatico:'Automático',fm_desligado:'Desligado',
 c_orc:'Orçamento atual: ≈ {tok} tokens de contexto (de {ctx}).',
 skillsHdr:'Fontes de conhecimento',
 p_preset:'Estilo de resposta',p_preset_d:'Escolha um preset ou crie o seu.',p_new:'Novo preset',p_edit:'Editar',p_del:'Excluir',
 p_name:'Nome',p_text:'Instruções',p_newT:'Novo preset',p_editT:'Editar preset',p_delConf:'Excluir o preset "{n}"?',p_max:'Limite de {n} presets atingido.',
 p_extra:'Instruções extras',p_extra_d:'Texto adicionado a todas as respostas (máx. {n} caracteres).',p_extraPh:'Ex.: Responda sempre com exemplos práticos.',
 p_proj:'Instruções por projeto',p_proj_d:'Só valem quando a conversa tem esse projeto ativo.',p_projNone:'Nenhum projeto disponível (ou o cofre está travado).',
 p_projPh:'Instruções para este projeto…',p_projSave:'Salvar',pr_builtin:'padrão',
 g_temp:'Temperatura',g_temp_d:'Baixa = previsível, alta = criativo. Vazio = padrão do modelo.',g_topp:'Top-p',g_topp_d:'Limita as palavras mais prováveis consideradas. Vazio = padrão do modelo.',
 g_max:'Máx. de tokens da resposta',g_max_d:'Tamanho máximo de cada resposta. Vazio = sem limite fixo.',g_seed:'Semente (seed)',g_seed_d:'Fixa o sorteio para respostas repetíveis. Vazio = aleatório.',
 g_note:'Cada modelo pode ter um perfil próprio (botão Perfil), que tem prioridade sobre estes valores.',
 v_active:'Sistema ativo',v_active_d:'Desligado = pausa: nada é aprendido e o modelo é descarregado da memória.',
 v_note:'Tudo roda e fica na sua máquina. Dados sensíveis (senhas, tokens, cartões, CPF) nunca são salvos automaticamente nem enviados ao modelo como memória.',
 ctxBtn:'Contexto',ctxTitle:'Contexto desta conversa',projLabel:'Projeto',projNone:'— sem projeto —',
 usedN:'Memórias usadas ({n})',usedPin:'Fixar',usedExc:'Excluir do contexto',usedPinned:'fixada',usedBudget:'Contexto: {u} de {o} caracteres',
 scopeT:'Escopo',scopeConv:'Só nesta conversa',scopeProj:'Em todo o projeto',
 pinTitle:'Fixar memória',pinMsg:'Ela entrará em todas as próximas respostas.',excTitle:'Excluir do contexto',excMsg:'Ela deixará de ser usada nas próximas respostas (a memória não é apagada).',
 pinDone:'Memória fixada.',excDone:'Memória excluída do contexto.',ctxErr:'Não foi possível atualizar o contexto.',
 ctxEmpty:'Nenhuma memória fixada ou excluída.',ctxConv:'Nesta conversa',ctxProj:'No projeto',ctxPinned:'Fixadas',ctxExcluded:'Excluídas',remove:'Remover',gone:'(memória removida)',close:'Fechar',
 factQ:'Guardar na memória?',factSave:'Salvar',factDiscard:'Descartar',factSaved:'Fato salvo.',factDup:'Já estava salvo.',factErr:'Não foi possível salvar este fato.',
 verTodos:'Ver todos os {n} modelos',verMenos:'Mostrar só os instalados',
 loading:'Carregando…',
 hwPre:'Seu PC',hwGpu:'{g} · {v} GB de VRAM',hwNoGpu:'sem GPU NVIDIA detectada',hwRam:'{r} GB de RAM',
 fitVram:'Cabe na sua VRAM ({v} GB)',fitParcial:'Vai usar a RAM também (mais lento)',fitCpu:'Roda na CPU/RAM (mais lento)',fitGrande:'Grande demais para este PC',
 fitNote:'Estimativa: tamanho do modelo + janela de contexto atual.',
 instHdr:'Instalados ({n})',instNone:'Nenhum modelo instalado ainda. Baixe um do catálogo abaixo.',catHdr:'Catálogo',
 catMore:'Ver mais {n} do catálogo',catLess:'Mostrar menos',catAll:'Todos os modelos do catálogo já estão instalados.',
 pullHdr:'Baixar outro modelo pelo nome',pullPh:'ex.: gemma3:4b',pullHint:'Qualquer modelo da biblioteca do Ollama (ollama.com/library).',pullBad:'Nome de modelo inválido.',
 isDefault:'Padrão',setDefault:'Tornar padrão',startOllama:'Iniciar Ollama',fora:'fora do catálogo',embedTag:'embeddings',
 c_sem:'Busca por significado',c_sem_d:'Usa o modelo de embeddings para achar memórias parecidas mesmo com outras palavras (um pouco mais lento).',
 usedWhy_relevante:'relevante',usedWhy_projeto:'do projeto',usedHint:'Enviadas ao modelo como contexto desta resposta.',usedManage:'Gerenciar memórias'
},
en:{
 s_modelo:'Model',s_memoria:'Memory & context',s_personalidade:'Personality & instructions',s_geracao:'Generation',
 s_privacidade:'Privacy & security',s_geral:'Language & theme',s_backup:'Backup & files',
 s_modelo_d:'Pick the model, see what fits your PC and tune the window.',
 s_memoria_d:'How Atlas chooses what the model remembers in each answer.',
 s_personalidade_d:'Tone and rules applied to every answer.',
 s_geracao_d:'Text generation parameters.',
 s_privacidade_d:'Pause, vault and API controls.',s_geral_d:'Language, theme and what the agent calls you.',s_backup_d:'Export and restore your data.',
 restaurar:'Restore defaults',restConf:'Restore this section to its defaults? Your data (memories, chats) is not deleted.',restOk:'Section restored.',
 saved:'Saved.',clamped:'Value adjusted to the allowed range ({min}–{max}).',invalid:'Invalid value.',range:'{min}–{max}',auto:'auto',
 f_todos:'All',f_geral:'General',f_codigo:'Code',f_raciocinio:'Reasoning',f_visao:'Vision',f_leve:'Light (low-end PC)',f_embed:'Embeddings',
 u_geral:'General',u_codigo:'Code',u_raciocinio:'Reasoning',u_visao:'Vision',u_leve:'Light',u_embed:'Embeddings',
 d_geral:'Everyday chat and tasks.',d_codigo:'Helps write and review code.',d_raciocinio:'Thinks step by step on hard problems (slower).',
 d_visao:'Understands images you attach.',d_leve:'Runs well on PCs with little memory.',d_embed:'Used for meaning-based search (memories and documents). Does not chat.',
 size:'Download',ram:'RAM ≈',ctxw:'Window',installed:'Installed',notInst:'Not installed',active:'In use',use:'Use',useEmbed:'Use for search',
 dl:'Download',dling:'Downloading…',retry:'Try again',del:'Delete',profile:'Profile',heavy:'May be heavy on this PC ({ram} GB RAM)',
 ollamaOff:'Start Ollama to download models.',dlErr:'Could not download {m}: {e}',dlNoOllama:'Ollama did not respond. Start it and try again.',
 dlDone:'{m} installed.',dlOffline:'No connection to download the model. Check your internet.',dlDisk:'Not enough disk space for this model.',
 pcRam:'This PC has {ram} GB of memory. RAM and size values are approximate.',
 prov:'Providers',provLocal:'local',provSoon:'External providers can be added here (none configured; no key is sent).',
 numctx:'Default context window',numctx_d:'Tokens the model reads at once. Larger = remembers more, uses more RAM. Each model has a ceiling.',
 profTitle:'Model profile',profMsg:'Applies only to {m}. Leave empty to use the default.',profCtx:'Context window (tokens)',profTemp:'Temperature',profTopP:'Top-p',profMax:'Max answer tokens',
 profCtxHint:'1024 – {max}',profSaved:'Profile saved.',profReset:'Profile reset.',
 c_pct:'Share of the window for context (%)',c_pct_d:'How much of the model window memories, graph and documents may use.',
 c_max:'Max memories per answer',c_max_d:'How many memories go into the context. 0 turns memories off.',
 c_rec:'Recency half-life (days)',c_rec_d:'Older memories weigh less in the ranking (half every N days).',
 c_hist:'Recent chat messages',c_hist_d:'How many earlier messages of this chat the model sees.',
 c_conv:'Recall other chats',c_conv_d:'Looks up snippets from earlier chats when relevant.',
 c_fatos:'Save durable facts',c_fatos_d:'When Atlas finds something lasting about you. Passwords, tokens and cards are never saved.',
 fm_perguntar:'Ask first',fm_automatico:'Automatic',fm_desligado:'Off',
 c_orc:'Current budget: ≈ {tok} context tokens (of {ctx}).',
 skillsHdr:'Knowledge sources',
 p_preset:'Answer style',p_preset_d:'Pick a preset or create your own.',p_new:'New preset',p_edit:'Edit',p_del:'Delete',
 p_name:'Name',p_text:'Instructions',p_newT:'New preset',p_editT:'Edit preset',p_delConf:'Delete preset "{n}"?',p_max:'Limit of {n} presets reached.',
 p_extra:'Extra instructions',p_extra_d:'Text added to every answer (max {n} characters).',p_extraPh:'E.g. Always answer with practical examples.',
 p_proj:'Per-project instructions',p_proj_d:'Only apply when the chat has that project active.',p_projNone:'No project available (or the vault is locked).',
 p_projPh:'Instructions for this project…',p_projSave:'Save',pr_builtin:'built-in',
 g_temp:'Temperature',g_temp_d:'Low = predictable, high = creative. Empty = model default.',g_topp:'Top-p',g_topp_d:'Limits the most likely words considered. Empty = model default.',
 g_max:'Max answer tokens',g_max_d:'Maximum length of each answer. Empty = no fixed limit.',g_seed:'Seed',g_seed_d:'Fixes the randomness for repeatable answers. Empty = random.',
 g_note:'Each model can have its own profile (Profile button), which takes priority over these values.',
 v_active:'System active',v_active_d:'Off = paused: nothing is learned and the model is unloaded from memory.',
 v_note:'Everything runs and stays on your machine. Sensitive data (passwords, tokens, cards, IDs) is never saved automatically nor sent to the model as memory.',
 ctxBtn:'Context',ctxTitle:'Context of this chat',projLabel:'Project',projNone:'— no project —',
 usedN:'Memories used ({n})',usedPin:'Pin',usedExc:'Exclude from context',usedPinned:'pinned',usedBudget:'Context: {u} of {o} characters',
 scopeT:'Scope',scopeConv:'This chat only',scopeProj:'Whole project',
 pinTitle:'Pin memory',pinMsg:'It will be included in every next answer.',excTitle:'Exclude from context',excMsg:'It will stop being used in next answers (the memory is not deleted).',
 pinDone:'Memory pinned.',excDone:'Memory excluded from context.',ctxErr:'Could not update the context.',
 ctxEmpty:'No pinned or excluded memories.',ctxConv:'In this chat',ctxProj:'In the project',ctxPinned:'Pinned',ctxExcluded:'Excluded',remove:'Remove',gone:'(memory removed)',close:'Close',
 factQ:'Save to memory?',factSave:'Save',factDiscard:'Dismiss',factSaved:'Fact saved.',factDup:'Already saved.',factErr:'Could not save this fact.',
 verTodos:'Show all {n} models',verMenos:'Show installed only',
 loading:'Loading…',
 hwPre:'Your PC',hwGpu:'{g} · {v} GB VRAM',hwNoGpu:'no NVIDIA GPU detected',hwRam:'{r} GB RAM',
 fitVram:'Fits in your VRAM ({v} GB)',fitParcial:'Will use RAM too (slower)',fitCpu:'Runs on CPU/RAM (slower)',fitGrande:'Too big for this PC',
 fitNote:'Estimate: model size + current context window.',
 instHdr:'Installed ({n})',instNone:'No model installed yet. Download one from the catalog below.',catHdr:'Catalog',
 catMore:'Show {n} more from the catalog',catLess:'Show less',catAll:'Every catalog model is already installed.',
 pullHdr:'Download another model by name',pullPh:'e.g. gemma3:4b',pullHint:'Any model from the Ollama library (ollama.com/library).',pullBad:'Invalid model name.',
 isDefault:'Default',setDefault:'Make default',startOllama:'Start Ollama',fora:'not in catalog',embedTag:'embeddings',
 c_sem:'Meaning-based search',c_sem_d:'Uses the embeddings model to find related memories even with different words (a bit slower).',
 usedWhy_relevante:'relevant',usedWhy_projeto:'from project',usedHint:'Sent to the model as context for this answer.',usedManage:'Manage memories'
},
es:{
 s_modelo:'Modelo',s_memoria:'Memoria y contexto',s_personalidade:'Personalidad e instrucciones',s_geracao:'Generación',
 s_privacidade:'Privacidad y seguridad',s_geral:'Idioma y tema',s_backup:'Copia y archivos',
 s_modelo_d:'Elige el modelo, mira qué cabe en tu PC y ajusta la ventana.',
 s_memoria_d:'Cómo Atlas elige lo que el modelo recuerda en cada respuesta.',
 s_personalidade_d:'Tono y reglas para todas las respuestas.',
 s_geracao_d:'Parámetros de generación del texto.',
 s_privacidade_d:'Control de pausa, caja fuerte y API.',s_geral_d:'Idioma, tema y cómo te llama el agente.',s_backup_d:'Exportar y restaurar tus datos.',
 restaurar:'Restaurar valores',restConf:'¿Restaurar esta sección a sus valores predeterminados? Tus datos (memorias, chats) no se borran.',restOk:'Sección restaurada.',
 saved:'Guardado.',clamped:'Valor ajustado al rango permitido ({min}–{max}).',invalid:'Valor no válido.',range:'{min}–{max}',auto:'auto',
 f_todos:'Todos',f_geral:'General',f_codigo:'Código',f_raciocinio:'Razonamiento',f_visao:'Visión',f_leve:'Ligeros (PC modesto)',f_embed:'Embeddings',
 u_geral:'General',u_codigo:'Código',u_raciocinio:'Razonamiento',u_visao:'Visión',u_leve:'Ligero',u_embed:'Embeddings',
 d_geral:'Conversación y tareas del día a día.',d_codigo:'Ayuda a escribir y revisar código.',d_raciocinio:'Piensa paso a paso en problemas difíciles (más lento).',
 d_visao:'Entiende las imágenes que adjuntas.',d_leve:'Funciona bien en PCs con poca memoria.',d_embed:'Se usa en la búsqueda por significado (memorias y documentos). No conversa.',
 size:'Descarga',ram:'RAM ≈',ctxw:'Ventana',installed:'Instalado',notInst:'No instalado',active:'En uso',use:'Usar',useEmbed:'Usar en la búsqueda',
 dl:'Descargar',dling:'Descargando…',retry:'Reintentar',del:'Eliminar',profile:'Perfil',heavy:'Puede ser pesado en este PC ({ram} GB de RAM)',
 ollamaOff:'Inicia Ollama para descargar modelos.',dlErr:'No se pudo descargar {m}: {e}',dlNoOllama:'Ollama no respondió. Inícialo e inténtalo de nuevo.',
 dlDone:'{m} instalado.',dlOffline:'Sin conexión para descargar el modelo. Revisa tu internet.',dlDisk:'No hay espacio en disco para este modelo.',
 pcRam:'Este PC tiene {ram} GB de memoria. Los valores de RAM y tamaño son aproximados.',
 prov:'Proveedores',provLocal:'local',provSoon:'Aquí se podrán añadir proveedores externos (ninguno configurado; no se envía ninguna clave).',
 numctx:'Ventana de contexto predeterminada',numctx_d:'Tokens que el modelo lee a la vez. Mayor = recuerda más, usa más RAM. Cada modelo tiene un tope.',
 profTitle:'Perfil del modelo',profMsg:'Solo vale para {m}. Déjalo vacío para usar el valor predeterminado.',profCtx:'Ventana de contexto (tokens)',profTemp:'Temperatura',profTopP:'Top-p',profMax:'Máx. de tokens de respuesta',
 profCtxHint:'1024 – {max}',profSaved:'Perfil guardado.',profReset:'Perfil restaurado.',
 c_pct:'Parte de la ventana para contexto (%)',c_pct_d:'Cuánto de la ventana del modelo pueden ocupar memorias, grafo y documentos.',
 c_max:'Máx. de memorias por respuesta',c_max_d:'Cuántas memorias entran en el contexto. 0 apaga las memorias.',
 c_rec:'Vida media de la recencia (días)',c_rec_d:'Las memorias más antiguas pesan menos en el ranking (la mitad cada N días).',
 c_hist:'Mensajes recientes del chat',c_hist_d:'Cuántos mensajes anteriores de este chat ve el modelo.',
 c_conv:'Recordar otras conversaciones',c_conv_d:'Busca fragmentos de chats anteriores cuando son relevantes.',
 c_fatos:'Guardar hechos duraderos',c_fatos_d:'Cuando Atlas encuentra algo duradero sobre ti. Nunca se guardan contraseñas, tokens ni tarjetas.',
 fm_perguntar:'Preguntar',fm_automatico:'Automático',fm_desligado:'Apagado',
 c_orc:'Presupuesto actual: ≈ {tok} tokens de contexto (de {ctx}).',
 skillsHdr:'Fuentes de conocimiento',
 p_preset:'Estilo de respuesta',p_preset_d:'Elige un preset o crea el tuyo.',p_new:'Nuevo preset',p_edit:'Editar',p_del:'Eliminar',
 p_name:'Nombre',p_text:'Instrucciones',p_newT:'Nuevo preset',p_editT:'Editar preset',p_delConf:'¿Eliminar el preset "{n}"?',p_max:'Límite de {n} presets alcanzado.',
 p_extra:'Instrucciones extra',p_extra_d:'Texto añadido a todas las respuestas (máx. {n} caracteres).',p_extraPh:'Ej.: Responde siempre con ejemplos prácticos.',
 p_proj:'Instrucciones por proyecto',p_proj_d:'Solo valen cuando el chat tiene ese proyecto activo.',p_projNone:'Ningún proyecto disponible (o la caja fuerte está bloqueada).',
 p_projPh:'Instrucciones para este proyecto…',p_projSave:'Guardar',pr_builtin:'integrado',
 g_temp:'Temperatura',g_temp_d:'Baja = predecible, alta = creativo. Vacío = valor del modelo.',g_topp:'Top-p',g_topp_d:'Limita las palabras más probables consideradas. Vacío = valor del modelo.',
 g_max:'Máx. de tokens de respuesta',g_max_d:'Longitud máxima de cada respuesta. Vacío = sin límite fijo.',g_seed:'Semilla (seed)',g_seed_d:'Fija el azar para respuestas repetibles. Vacío = aleatorio.',
 g_note:'Cada modelo puede tener su propio perfil (botón Perfil), que tiene prioridad sobre estos valores.',
 v_active:'Sistema activo',v_active_d:'Apagado = pausa: no se aprende nada y el modelo se descarga de la memoria.',
 v_note:'Todo corre y se queda en tu máquina. Los datos sensibles (contraseñas, tokens, tarjetas, documentos) nunca se guardan automáticamente ni se envían al modelo como memoria.',
 ctxBtn:'Contexto',ctxTitle:'Contexto de esta conversación',projLabel:'Proyecto',projNone:'— sin proyecto —',
 usedN:'Memorias usadas ({n})',usedPin:'Fijar',usedExc:'Excluir del contexto',usedPinned:'fijada',usedBudget:'Contexto: {u} de {o} caracteres',
 scopeT:'Alcance',scopeConv:'Solo esta conversación',scopeProj:'Todo el proyecto',
 pinTitle:'Fijar memoria',pinMsg:'Entrará en todas las próximas respuestas.',excTitle:'Excluir del contexto',excMsg:'Dejará de usarse en las próximas respuestas (la memoria no se borra).',
 pinDone:'Memoria fijada.',excDone:'Memoria excluida del contexto.',ctxErr:'No se pudo actualizar el contexto.',
 ctxEmpty:'No hay memorias fijadas ni excluidas.',ctxConv:'En esta conversación',ctxProj:'En el proyecto',ctxPinned:'Fijadas',ctxExcluded:'Excluidas',remove:'Quitar',gone:'(memoria eliminada)',close:'Cerrar',
 factQ:'¿Guardar en la memoria?',factSave:'Guardar',factDiscard:'Descartar',factSaved:'Hecho guardado.',factDup:'Ya estaba guardado.',factErr:'No se pudo guardar este hecho.',
 verTodos:'Ver los {n} modelos',verMenos:'Mostrar solo los instalados',
 loading:'Cargando…',
 hwPre:'Tu PC',hwGpu:'{g} · {v} GB de VRAM',hwNoGpu:'sin GPU NVIDIA detectada',hwRam:'{r} GB de RAM',
 fitVram:'Cabe en tu VRAM ({v} GB)',fitParcial:'Usará también la RAM (más lento)',fitCpu:'Corre en CPU/RAM (más lento)',fitGrande:'Demasiado grande para este PC',
 fitNote:'Estimación: tamaño del modelo + ventana de contexto actual.',
 instHdr:'Instalados ({n})',instNone:'Aún no hay modelos instalados. Descarga uno del catálogo.',catHdr:'Catálogo',
 catMore:'Ver {n} más del catálogo',catLess:'Mostrar menos',catAll:'Todos los modelos del catálogo ya están instalados.',
 pullHdr:'Descargar otro modelo por nombre',pullPh:'ej.: gemma3:4b',pullHint:'Cualquier modelo de la biblioteca de Ollama (ollama.com/library).',pullBad:'Nombre de modelo no válido.',
 isDefault:'Predeterminado',setDefault:'Usar por defecto',startOllama:'Iniciar Ollama',fora:'fuera del catálogo',embedTag:'embeddings',
 c_sem:'Búsqueda por significado',c_sem_d:'Usa el modelo de embeddings para encontrar memorias parecidas aunque usen otras palabras (un poco más lento).',
 usedWhy_relevante:'relevante',usedWhy_projeto:'del proyecto',usedHint:'Enviadas al modelo como contexto de esta respuesta.',usedManage:'Gestionar memorias'
}};

function t(k,v){var s=(D[LANG]&&D[LANG][k])||D.pt[k]||k;if(v)for(var x in v)s=s.split('{'+x+'}').join(v[x]);return s;}
function h(tag,attrs,kids){var e=document.createElement(tag);
  if(attrs)Object.keys(attrs).forEach(function(k){var v=attrs[k];if(v==null)return;
    if(k==='class')e.className=v;else if(k==='text')e.textContent=v;else if(k.slice(0,2)==='on')e[k]=v;else if(k==='value'||k==='checked'||k==='disabled'||k==='tabIndex')e[k]=v;else e.setAttribute(k,v);});
  (kids||[]).forEach(function(c){if(c!=null)e.append(c);});return e;}
var $=function(s){return document.querySelector(s);};
function est(){return H.estado();}
function cfg(){return est().config;}
function toast(m,k){AtlasUI.toast(m,{kind:k||'info'});}
function lim(){return (est().ajustes)||{};}

// ── helpers de UI ──
function linha(titulo,desc,controle){
  return h('div',{class:'skill'},[h('div',{class:'info'},[h('div',{class:'nm',text:titulo}),desc?h('div',{class:'d',text:desc}):null]),controle]);
}
function interruptor(on,cb,rotulo){
  var s=h('div',{class:'sw'+(on?' on':''),role:'switch','aria-checked':String(!!on),tabIndex:0,'aria-label':rotulo||''});
  s.onclick=function(){cb(!on);};
  s.onkeydown=function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();s.click();}};
  return s;
}
// campo numérico validado: min/max, inteiro opcional, vazio = null (se anulável)
function numero(o){
  var inp=h('input',{type:'number',class:'num',min:o.min,max:o.max,step:o.step||(o.int?1:0.05),value:o.value==null?'':o.value,'aria-label':o.label});
  if(o.nullable)inp.placeholder=t('auto');
  inp.onchange=function(){
    var raw=inp.value.trim();
    if(raw===''){if(o.nullable){o.salvar(null);}else{inp.value=o.value==null?'':o.value;}return;}
    var n=Number(raw);
    if(!isFinite(n)){toast(t('invalid'),'err');inp.value=o.value==null?'':o.value;return;}
    var c=Math.min(o.max,Math.max(o.min,n));if(o.int)c=Math.round(c);
    if(c!==n){toast(t('clamped',{min:o.min,max:o.max}),'info');}
    inp.value=c;o.salvar(c);
  };
  var dica=h('span',{class:'rg',text:t('range',{min:o.min,max:o.max})});
  return h('div',{class:'numbox'},[inp,dica]);
}
function btn(txt,cls,fn){var b=h('button',{type:'button',class:cls||'baixar',text:txt});if(fn)b.onclick=fn;return b;}

// ── títulos fixos (chamado em fixos()) ──
var SECOES=['modelo','memoria','personalidade','geracao','privacidade','geral','backup'];
function titulos(){
  SECOES.forEach(function(s){
    var sm=$('#sec-'+s+' > summary');if(!sm)return;
    sm.textContent='';sm.append(h('span',{class:'stt',text:t('s_'+s)}),h('span',{class:'std',text:t('s_'+s+'_d')}));
    var r=$('#sec-'+s+' .rest');if(r)r.textContent='↺ '+t('restaurar');
  });
  var b=$('#ctxBtn');if(b){b.textContent='🧠 '+t('ctxBtn');b.title=t('ctxTitle');}
  var pl=$('#projLabel');if(pl)pl.textContent=t('projLabel');
}

async function restaurar(sec){
  if(!await H.conf(t('restConf'),{ok:t('restaurar')}))return;
  try{
    var r=await fetch('/api/config/restaurar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({secao:sec})});
    var o=await r.json();if(!r.ok||!o.ok)throw new Error(o.erro||('HTTP '+r.status));
    H.setEstado(o);toast(t('restOk'),'ok');
  }catch(e){toast(String(e.message||e),'err');}
}

// ── seção Modelo ──
var CAT_MAIS=false;
var RE_MODELO=/^[A-Za-z0-9][A-Za-z0-9_.\-\/]{0,79}(:[A-Za-z0-9_.\-]{1,40})?$/;
function ramPC(){var hw=est().hardware||{};return hw.ram_gb||H.ramTotalGB||0;}
function vramPC(){return (est().hardware||{}).vram_gb||0;}
function fmtGB(n){return (n>=10?Math.round(n):Math.round(n*10)/10)+' GB';}
function fmtCtx(n){return n>=1024?(Math.round(n/102.4)/10)+'k':String(n);}
function semLatest(n){return String(n||'').replace(/:latest$/,'');}
function filtrar(lista){
  if(window.AtlasModelos)return AtlasModelos.filtrar(lista,FILTRO);   // filtros por nível/ferramentas (modelos.js)
  if(FILTRO==='todos'||FILTRO==='recomendados')return lista;
  return lista.filter(function(m){return (m.usos||[]).indexOf(FILTRO)>=0;});
}
// selo "cabe na VRAM / usa RAM / grande demais" (estimativa do servidor)
function selo(cabe,embed){
  if(!cabe||embed)return null;
  var txt=cabe==='vram'?'✓ '+t('fitVram',{v:Math.round(vramPC()*10)/10}):cabe==='parcial'?(vramPC()?'≈ '+t('fitParcial'):'≈ '+t('fitCpu')):'✕ '+t('fitGrande');
  return h('span',{class:'fit '+cabe,text:txt,title:t('fitNote')});
}
function linhaHardware(){
  var hw=est().hardware||{},partes=[];
  if(hw.vram_gb)partes.push(t('hwGpu',{g:hw.gpu||'GPU',v:hw.vram_gb}));else partes.push(t('hwNoGpu'));
  if(ramPC())partes.push(t('hwRam',{r:Math.round(ramPC())}));
  return h('div',{class:'hw'},[h('strong',{text:t('hwPre')+': '}),document.createTextNode(partes.join(' · '))]);
}
function renderModelo(){
  var e=est(),c=cfg();
  var info=$('#mInfo');info.textContent='';
  info.append(linhaHardware());
  if(window.AtlasModelos)AtlasModelos.hardware(info,e,H);              // GPUs, CPU e ajuste manual (modelos.js)
  if(!e.ollama_online){
    var av=h('div',{class:'mwarn'},[h('span',{text:t('ollamaOff')})]);
    if(e.ollama_instalado&&H.iniciarOllama)av.append(btn(t('startOllama'),'baixar',function(){H.iniciarOllama();}));
    info.append(av);
  }
  // janela padrão
  var mx=(lim().num_ctx||[1024,32768,4096]);
  var ctxRow=$('#mCtxRow');ctxRow.textContent='';
  ctxRow.append(linha(t('numctx'),t('numctx_d'),numero({label:t('numctx'),min:mx[0],max:mx[1],int:true,step:1024,value:c.num_ctx||mx[2],salvar:function(v){H.salvar({num_ctx:v});}})));

  var box=$('#modelos');box.textContent='';
  // 1) instalados (inclusive fora do catálogo)
  var inst=(e.instalados_info||[]).slice().sort(function(a,b){
    return (a.tipo==='embed')-(b.tipo==='embed')||(b.ativo-a.ativo)||a.nome.localeCompare(b.nome);});
  if(e.ollama_online){
    box.append(h('h3',{class:'sub',text:t('instHdr',{n:inst.length})}));
    if(!inst.length)box.append(h('div',{class:'ui-empty',text:t('instNone')}));
    inst.forEach(function(m){box.append(cartaoInstalado(m,e,c));});
    // 2) baixar pelo nome
    box.append(h('h3',{class:'sub',text:t('pullHdr')}));
    var inp=h('input',{type:'text',class:'sel',placeholder:t('pullPh'),'aria-label':t('pullHdr'),spellcheck:'false',autocomplete:'off'});
    var pr=h('div',{class:'prog'},[h('div')]);
    var bp=btn(t('dl'),'baixar',function(){
      var nome=inp.value.trim();
      if(!RE_MODELO.test(nome)||nome.indexOf('..')>=0){toast(t('pullBad'),'err');inp.focus();return;}
      H.baixar(nome,bp,pr);});
    inp.onkeydown=function(ev){if(ev.key==='Enter'){ev.preventDefault();bp.click();}};
    box.append(h('div',{class:'pullrow'},[inp,bp]),pr,h('div',{class:'d',text:t('pullHint')}));
  }
  // 3) catálogo: só o que ainda não está instalado
  box.append(h('h3',{class:'sub',text:t('catHdr')}));
  var fb=h('div',{class:'seg',id:'mFilter2',role:'group'});
  (window.AtlasModelos?AtlasModelos.FILTROS:['todos','geral','codigo','raciocinio','visao','leve','embed']).forEach(function(f){
    var b=h('button',{type:'button',class:FILTRO===f?'on':'',text:window.AtlasModelos?AtlasModelos.rotuloFiltro(f):t('f_'+f),'aria-pressed':String(FILTRO===f)});
    b.onclick=function(){FILTRO=f;renderModelo();};fb.append(b);
  });
  box.append(fb);
  if(FILTRO==='recomendados'&&window.AtlasModelos){AtlasModelos.recomendados(box,e,cartao,cartaoInstalado);}
  else{
  var resto=filtrar((e.catalogo||[]).filter(function(m){return !m.instalado;}));
  var ordem={vram:0,parcial:1,grande:2};
  resto.sort(function(a,b){return ((a.tipo==='embed')-(b.tipo==='embed'))||((ordem[a.cabe]||0)-(ordem[b.cabe]||0))||(b.gb-a.gb);});
  if(!resto.length)box.append(h('div',{class:'ui-empty',text:t('catAll')}));
  var limite=CAT_MAIS||FILTRO!=='todos'?resto.length:6;
  resto.slice(0,limite).forEach(function(m){box.append(cartao(m,e,c));});
  if(resto.length>6&&FILTRO==='todos')box.append(btn(CAT_MAIS?t('catLess'):t('catMore',{n:resto.length-6}),'baixar',function(){CAT_MAIS=!CAT_MAIS;renderModelo();}));
  }
  // provedores (estrutura)
  var pv=$('#mProv');pv.textContent='';
  var pr2=e.provedores||{};
  pv.append(h('div',{class:'nm',text:t('prov')}));
  Object.keys(pr2).forEach(function(k){pv.append(h('span',{class:'tag',text:(pr2[k].rotulo||k)+(pr2[k].local?' · '+t('provLocal'):'')}));});
  pv.append(h('div',{class:'d',text:t('provSoon')}));
  var mf=$('#mFilter');if(mf)mf.textContent='';
}
function cartaoInstalado(m,e,c){
  var embed=m.tipo==='embed',cat=m.catalogo?(e.catalogo||[]).find(function(x){return x.nome===m.catalogo;}):null;
  var div=h('div',{class:'modelo inst'+(m.ativo?' sel':'')});
  var rot=cat?cat.rotulo:semLatest(m.nome);
  var top=h('div',{class:'top'},[h('span',{class:'rot',text:rot})]);
  if(m.ativo)top.append(h('span',{class:'tag pin',text:embed?t('active'):t('isDefault')}));
  if(rot!==m.nome)top.append(h('span',{class:'nm',text:m.nome}));
  div.append(top);
  var meta=[m.gb?fmtGB(m.gb):'',m.parametros,m.quant,m.familia].filter(Boolean).join(' · ');
  var tags=h('div',{class:'tags'});
  if(embed)tags.append(h('span',{class:'tag',text:t('embedTag')}));
  if(cat)(cat.usos||[]).forEach(function(u){if(u!=='embed')tags.append(h('span',{class:'tag',text:t('u_'+u)}));});
  else tags.append(h('span',{class:'tag',text:t('fora')}));
  div.append(tags);
  if(meta)div.append(h('div',{class:'meta',text:meta}));
  var s=selo(m.cabe,embed);if(s)div.append(s);
  if(window.AtlasModelos)AtlasModelos.extras(div,cat||{tipo:m.tipo},e,m);   // nível, ferramentas, medido (modelos.js)
  var acoes=h('div',{class:'acts2'});
  if(!m.ativo)acoes.append(btn(embed?t('useEmbed'):t('setDefault'),'baixar',function(ev){ev.stopPropagation();usar({nome:m.nome,tipo:m.tipo});}));
  if(!embed)acoes.append(btn(t('profile'),'baixar',function(ev){ev.stopPropagation();
    perfil(cat||{nome:m.nome,rotulo:semLatest(m.nome),ctx_max:32768,temp:0.7});}));
  acoes.append(btn('🗑 '+t('del'),'baixar danger',function(ev){ev.stopPropagation();H.excluir(m.nome);}));
  div.append(acoes);
  return div;
}
function cartao(m,e,c){
  var embed=m.tipo==='embed';
  var div=h('div',{class:'modelo'});
  div.append(h('div',{class:'top'},[h('span',{class:'rot',text:m.rotulo}),h('span',{class:'nm',text:m.nome})]));
  var tags=h('div',{class:'tags'});
  (m.usos||[]).forEach(function(u){tags.append(h('span',{class:'tag',text:t('u_'+u)}));});
  div.append(tags);
  var principal=(m.usos||[])[0]||'geral';
  var legado=(window.mdesc&&window.mdesc(m.nome))||'';
  div.append(h('div',{class:'desc',text:legado||t('d_'+principal)}));
  div.append(h('div',{class:'meta',text:t('size')+' '+fmtGB(m.gb)+' · '+t('ram')+' '+fmtGB(m.ram)+(embed?'':' · '+t('ctxw')+' '+fmtCtx(m.ctx)+'/'+fmtCtx(m.ctx_max))}));
  var s=selo(m.cabe,embed);
  if(s)div.append(s);
  else if(ramPC()&&m.ram>ramPC()*0.85)div.append(h('div',{class:'d warn',text:'⚠ '+t('heavy',{ram:Math.round(ramPC())})}));
  if(window.AtlasModelos)AtlasModelos.extras(div,m,e,null);              // nível, ferramentas, variantes (modelos.js)
  if(e.ollama_online){
    var acoes=h('div',{class:'acts2'});
    var bd=btn(t('dl'),'baixar');var pr=h('div',{class:'prog'},[h('div')]);
    bd.onclick=function(ev){ev.stopPropagation();H.baixar(window.AtlasModelos?AtlasModelos.tag(m):m.nome,bd,pr);};
    acoes.append(bd);div.append(acoes);div.append(pr);
  }
  return div;
}
async function usar(m){
  if(m.tipo==='embed'){
    await H.salvar({embed:m.nome});
    try{await fetch('/api/docs/reindexar',{method:'POST'});}catch(e){}
  }else await H.salvar({modelo:m.nome});
}
async function perfil(m){
  var p=(cfg().perfis_modelo||{})[m.nome]||{};
  var teto=Math.min(32768,m.ctx_max);
  var r=await AtlasUI.form({title:t('profTitle')+' — '+m.rotulo,message:t('profMsg',{m:m.nome}),okText:t('p_projSave'),cancelText:t('close'),
    fields:[{name:'num_ctx',type:'number',label:t('profCtx'),min:1024,max:teto,step:512,value:p.num_ctx,hint:t('profCtxHint',{max:teto})},
            {name:'temperatura',type:'number',label:t('profTemp'),min:0,max:2,step:0.05,value:p.temperatura,hint:t('range',{min:0,max:2})+' · '+m.temp},
            {name:'top_p',type:'number',label:t('profTopP'),min:0.05,max:1,step:0.05,value:p.top_p,hint:t('range',{min:0.05,max:1})},
            {name:'max_tokens',type:'number',label:t('profMax'),min:16,max:8192,step:16,value:p.max_tokens,hint:t('range',{min:16,max:8192})}]
            .concat(window.AtlasModelos?[AtlasModelos.campoPerfil(p)]:[])});
  if(!r)return;
  if(window.AtlasModelos)r.ferramentas=AtlasModelos.valorPerfil(r.ferramentas);
  var obj={};Object.keys(r).forEach(function(k){if(r[k]!=null)obj[k]=r[k];});
  await H.salvar({perfis_modelo:(function(){var o={};o[m.nome]=Object.keys(obj).length?obj:null;return o;})()});
  toast(Object.keys(obj).length?t('profSaved'):t('profReset'),'ok');
}

// ── seção Memória e contexto ──
function renderContexto(){
  var c=cfg(),k=c.contexto||{},L=lim().contexto||{};
  var box=$('#ctxBox');box.textContent='';
  function n(chave,rot,desc,def){var r=L[chave]||[0,100];
    box.append(linha(t(rot),t(desc),numero({label:t(rot),min:r[0],max:r[1],int:true,step:1,value:k[chave]==null?def:k[chave],
      salvar:function(v){var o={};o[chave]=v;H.salvar({contexto:o});}})));}
  n('ctx_pct','c_pct','c_pct_d',35);n('max_memorias','c_max','c_max_d',6);n('recencia_dias','c_rec','c_rec_d',30);n('hist_msgs','c_hist','c_hist_d',6);
  box.append(linha(t('c_conv'),t('c_conv_d'),interruptor(k.incluir_conversas!==false,function(v){H.salvar({contexto:{incluir_conversas:v}});},t('c_conv'))));
  box.append(linha(t('c_sem'),t('c_sem_d'),interruptor(!!k.busca_semantica,function(v){H.salvar({contexto:{busca_semantica:v}});},t('c_sem'))));
  var fm=h('div',{class:'seg',id:'fatosSeg'});
  box.append(h('div',{class:'skill col'},[h('div',{class:'info'},[h('div',{class:'nm',text:t('c_fatos')}),h('div',{class:'d',text:t('c_fatos_d')})]),fm]));
  H.seg('#fatosSeg',(lim().fatos_modos||['perguntar','automatico','desligado']),k.fatos_modo||'perguntar',function(v){return t('fm_'+v);},function(v){H.salvar({contexto:{fatos_modo:v}});});
  if(window.AtlasModelos)AtlasModelos.secaoFerramentas(box,H,c,{linha:linha,interruptor:interruptor,numero:numero});
  // orçamento estimado para o modelo ativo
  var ctx=efetivoCtx();var saida=Math.min(1024,Math.floor(ctx/4));
  var tok=Math.max(0,Math.round((ctx-saida-300)*(k.ctx_pct||35)/100));
  box.append(h('div',{class:'d budget',text:t('c_orc',{tok:tok,ctx:ctx})}));
}
function efetivoCtx(){
  var c=cfg(),m=(est().catalogo||[]).find(function(x){return x.ativo&&x.tipo!=='embed';})||{ctx_max:32768};
  var p=(c.perfis_modelo||{})[m.nome]||{};
  return Math.max(1024,Math.min(32768,m.ctx_max||32768,p.num_ctx||c.num_ctx||4096));
}

// ── seção Personalidade ──
function presetsTodos(){
  var pre=((lim().presets)||{}),out=[];
  Object.keys(pre).forEach(function(id){out.push({id:id,nome:pre[id].nome[LANG]||pre[id].nome.pt,texto:pre[id].texto[LANG]||pre[id].texto.pt,builtin:true});});
  ((cfg().instrucoes||{}).personalizados||[]).forEach(function(p){out.push({id:p.id,nome:p.nome,texto:p.texto,builtin:false});});
  return out;
}
function renderPersonalidade(){
  var c=cfg(),ins=c.instrucoes||{},box=$('#persBox');box.textContent='';
  box.append(h('div',{class:'nm',text:t('p_preset')}),h('div',{class:'d',text:t('p_preset_d')}));
  var lista=h('div',{class:'presets',role:'radiogroup','aria-label':t('p_preset')});
  presetsTodos().forEach(function(p){
    var on=(ins.preset||'padrao')===p.id;
    var card=h('div',{class:'preset'+(on?' sel':''),role:'radio','aria-checked':String(on),tabIndex:0});
    card.append(h('div',{class:'top'},[h('span',{class:'rot',text:p.nome}),p.builtin?h('span',{class:'tag',text:t('pr_builtin')}):null]));
    if(p.texto)card.append(h('div',{class:'d',text:p.texto}));
    card.onclick=function(){H.salvar({instrucoes:{preset:p.id}});};
    card.onkeydown=function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();card.click();}};
    if(!p.builtin){
      var a=h('div',{class:'acts2'});
      var be=btn(t('p_edit'),'baixar',function(ev){ev.stopPropagation();editarPreset(p);});
      var bd=btn(t('p_del'),'baixar danger',function(ev){ev.stopPropagation();excluirPreset(p);});
      a.append(be,bd);card.append(a);
    }
    lista.append(card);
  });
  box.append(lista);
  box.append(btn('＋ '+t('p_new'),'baixar',function(){editarPreset(null);}));
  // texto extra
  var ex=h('textarea',{class:'ta',rows:3,maxlength:lim().max_extra||2000,placeholder:t('p_extraPh'),'aria-label':t('p_extra'),value:ins.extra||''});
  ex.onchange=function(){H.salvar({instrucoes:{extra:ex.value}});};
  box.append(h('div',{class:'nm sp',text:t('p_extra')}),h('div',{class:'d',text:t('p_extra_d',{n:lim().max_extra||2000})}),ex);
  // por projeto
  box.append(h('div',{class:'nm sp',text:t('p_proj')}),h('div',{class:'d',text:t('p_proj_d')}));
  var pj=h('div',{id:'persProj'});box.append(pj);
  renderPersProj(pj);
}
async function carregarProjetos(){
  try{var r=await fetch('/api/projetos');PROJ=r.ok?await r.json():[];}catch(e){PROJ=[];}
  if(!Array.isArray(PROJ))PROJ=[];
  return PROJ;
}
async function renderPersProj(pj){
  await carregarProjetos();pj.textContent='';
  if(!PROJ.length){pj.append(h('div',{class:'d',text:t('p_projNone')}));return;}
  var ip=cfg().instrucoes_projeto||{};
  var sel=h('select',{class:'sel','aria-label':t('p_proj')});
  PROJ.forEach(function(p){sel.append(h('option',{value:p.id,text:'\u00A0\u00A0'.repeat(p.depth||0)+p.name+(ip[p.id]?' ✎':'')}));});
  var ta=h('textarea',{class:'ta',rows:3,maxlength:lim().max_extra||2000,placeholder:t('p_projPh'),'aria-label':t('p_proj')});
  function atual(){ta.value=ip[sel.value]||'';}
  sel.onchange=atual;atual();
  var sv=btn(t('p_projSave'),'baixar',function(){var o={};o[sel.value]=ta.value;H.salvar({instrucoes_projeto:o}).then(function(){toast(t('saved'),'ok');});});
  pj.append(sel,ta,sv);
}
async function editarPreset(p){
  var ps=(cfg().instrucoes||{}).personalizados||[],max=lim().max_presets||12;
  if(!p&&ps.length>=max){toast(t('p_max',{n:max}),'err');return;}
  var r=await AtlasUI.form({title:p?t('p_editT'):t('p_newT'),okText:t('p_projSave'),cancelText:t('close'),
    fields:[{name:'nome',label:t('p_name'),value:p?p.nome:'',required:true,maxLength:40},
            {name:'texto',type:'textarea',label:t('p_text'),value:p?p.texto:'',required:true,maxLength:lim().max_extra||2000}]});
  if(!r)return;
  var nova=ps.map(function(x){return (p&&x.id===p.id)?{id:x.id,nome:r.nome,texto:r.texto}:x;});
  if(!p)nova.push({nome:r.nome,texto:r.texto});
  await H.salvar({instrucoes:{personalizados:nova}});
  if(!p){ // seleciona o recém criado
    var ult=((cfg().instrucoes||{}).personalizados||[]).slice(-1)[0];
    if(ult)await H.salvar({instrucoes:{preset:ult.id}});
  }
}
async function excluirPreset(p){
  if(!await H.conf(t('p_delConf',{n:p.nome}),{ok:t('p_del'),danger:true}))return;
  var ps=((cfg().instrucoes||{}).personalizados||[]).filter(function(x){return x.id!==p.id;});
  await H.salvar({instrucoes:{personalizados:ps}});
}

// ── seção Geração ──
function renderGeracao(){
  var g=cfg().geracao||{},L=lim().geracao||{},box=$('#genBox');box.textContent='';
  function n(chave,rot,desc,int,step){var r=L[chave]||[0,1];
    box.append(linha(t(rot),t(desc),numero({label:t(rot),min:r[0],max:r[1],int:int,step:step,nullable:true,value:g[chave],
      salvar:function(v){var o={};o[chave]=v;H.salvar({geracao:o});}})));}
  n('temperatura','g_temp','g_temp_d',false,0.05);n('top_p','g_topp','g_topp_d',false,0.05);
  n('max_tokens','g_max','g_max_d',true,16);n('seed','g_seed','g_seed_d',true,1);
  box.append(h('div',{class:'d',text:t('g_note')}));
}

// ── seção Privacidade ──
function renderPrivacidade(){
  var c=cfg(),box=$('#privBox');box.textContent='';
  var on=c.ativo!==false;
  box.append(linha(t('v_active'),t('v_active_d'),interruptor(on,function(v){H.salvar({ativo:v});},t('v_active'))));
  box.append(h('div',{class:'d note',text:t('v_note')}));
}

function render(){
  if(!H||!est())return;
  var ae=document.activeElement,chave=(ae&&ae.closest&&ae.closest('#cfg')&&ae.getAttribute)?ae.getAttribute('aria-label'):null;
  titulos();renderModelo();renderContexto();renderPersonalidade();renderGeracao();renderPrivacidade();
  if(chave){var alvo=[].slice.call(document.querySelectorAll('#cfg [aria-label]')).filter(function(x){return x.getAttribute('aria-label')===chave;})[0];
    if(alvo&&alvo.focus)alvo.focus();}   // mantém o foco do teclado depois de salvar
}

// ── chat: projeto ativo, memórias usadas, contexto, sugestão de fato ──
var CH={
  async carregarProjetos(){await carregarProjetos();CH.renderProjeto();},
  renderProjeto(){
    var sel=$('#projSel');if(!sel)return;
    sel.textContent='';sel.append(h('option',{value:'',text:t('projNone')}));
    PROJ.forEach(function(p){sel.append(h('option',{value:p.id,text:'\u00A0\u00A0'.repeat(p.depth||0)+p.name}));});
    sel.value=CH.projetoAtual||'';
    if(sel.value!==(CH.projetoAtual||''))sel.value='';
  },
  projetoAtual:'',
  definirProjetoDoChat(pid){CH.projetoAtual=pid||'';CH.renderProjeto();},
  async trocarProjeto(pid){
    var cid=H.chat();if(!cid)return;
    try{await fetch('/api/chats/'+cid+'/ctx',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({projeto:pid||null})});
      CH.projetoAtual=pid||'';}catch(e){toast(t('ctxErr'),'err');}
  },
  parseHeader(v){if(!v)return null;try{return JSON.parse(decodeURIComponent(v));}catch(e){return null;}},
  // <details> colapsável sob a resposta
  usadas(bubbleEl,mems,info){
    var bub=bubbleEl.closest?bubbleEl.closest('.bubble'):bubbleEl;if(!bub)return;
    var velho=bub.querySelector('details.usadas');if(velho)velho.remove();
    if(!mems||!mems.length)return;
    var d=h('details',{class:'usadas'});
    var nFix=mems.filter(function(m){return m.f;}).length;
    d.append(h('summary',{text:'🧠 '+t('usedN',{n:mems.length})+(nFix?' · 📌 '+nFix:'')}));
    d.append(h('div',{class:'uhint',text:t('usedHint')}));
    var ul=h('ul');
    mems.forEach(function(m){
      var li=h('li');
      var txt=h('span',{class:'mt'});
      txt.append(h('span',{text:m.t||''}));
      var meta=h('span',{class:'mm'});
      if(m.f)meta.append(h('span',{class:'tag pin',text:'📌 '+t('usedPinned')}));
      else if(m.w&&m.w!=='fixada')meta.append(h('span',{class:'tag why',text:t('usedWhy_'+m.w)}));
      if(m.p)meta.append(h('span',{class:'tag',text:'📁 '+m.p}));
      txt.append(meta);
      li.append(txt);
      var a=h('span',{class:'ma'});
      a.append(h('button',{type:'button',class:'ic',title:t('usedPin'),'aria-label':t('usedPin'),text:'📌',onclick:function(){CH.acao('fixar',m);}}),
               h('button',{type:'button',class:'ic',title:t('usedExc'),'aria-label':t('usedExc'),text:'🚫',onclick:function(){CH.acao('excluir',m);}}));
      li.append(a);ul.append(li);
    });
    d.append(ul);
    var rod=h('div',{class:'ufoot'});
    if(info&&info.orcamento)rod.append(h('span',{text:t('usedBudget',{u:info.usado||0,o:info.orcamento})}));
    rod.append(h('a',{href:'/memorias',text:t('usedManage')+' →'}));
    d.append(rod);
    bub.append(d);
  },
  async acao(tipo,m){
    var cid=H.chat();if(!cid)return;
    var campos=[];
    if(CH.projetoAtual)campos.push({name:'escopo',type:'select',label:t('scopeT'),value:'conversa',options:[{v:'conversa',l:t('scopeConv')},{v:'projeto',l:t('scopeProj')}]});
    var r=await AtlasUI.form({title:tipo==='fixar'?t('pinTitle'):t('excTitle'),message:(m.t?'“'+m.t+'”\n\n':'')+(tipo==='fixar'?t('pinMsg'):t('excMsg')),
      fields:campos,okText:tipo==='fixar'?t('usedPin'):t('usedExc'),cancelText:t('close'),danger:tipo==='excluir'});
    if(!r)return;
    var ok=await CH.enviarAcao(cid,tipo,m.id,r.escopo||'conversa');
    toast(ok?(tipo==='fixar'?t('pinDone'):t('excDone')):t('ctxErr'),ok?'ok':'err');
  },
  async enviarAcao(cid,acao,id,escopo){
    try{var r=await fetch('/api/chats/'+cid+'/ctx',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({acao:acao,id:id,escopo:escopo})});
      return r.ok;}catch(e){return false;}
  },
  async abrirContexto(){
    var cid=H.chat();if(!cid)return;
    var d=null;try{var r=await fetch('/api/chats/'+cid+'/ctx');d=r.ok?await r.json():null;}catch(e){}
    if(!d){toast(t('ctxErr'),'err');return;}
    AtlasUI.modal({title:t('ctxTitle'),wide:true,build:function(box,fechar){
      var corpo=h('div',{class:'ctxlist'});box.append(corpo);
      function bloco(titulo,escopo,grupo){
        var itens=[];
        ['fixas','excluidas'].forEach(function(g){(grupo[g]||[]).forEach(function(m){itens.push({g:g,m:m});});});
        if(!itens.length)return 0;
        corpo.append(h('h3',{text:titulo}));
        itens.forEach(function(it){
          var li=h('div',{class:'ctxitem'});
          li.append(h('span',{class:'tag '+(it.g==='fixas'?'pin':''),text:it.g==='fixas'?'📌 '+t('ctxPinned'):'🚫 '+t('ctxExcluded')}));
          li.append(h('span',{class:'mt',text:it.m.t||t('gone')}));
          li.append(btn(t('remove'),'baixar',async function(){
            var ok=await CH.enviarAcao(cid,'limpar',it.m.id,escopo);
            if(ok){fechar(null);CH.abrirContexto();}else toast(t('ctxErr'),'err');}));
          corpo.append(li);
        });
        return itens.length;
      }
      var n=bloco(t('ctxConv'),'conversa',d.conversa||{})+bloco(t('ctxProj'),'projeto',d.projeto_regras||{});
      if(!n)corpo.append(h('div',{class:'ui-empty',text:t('ctxEmpty')}));
      box.append(h('div',{class:'ui-actions'},[btn(t('close'),'ui-btn',function(){fechar(null);})]));
    }});
  },
  sugestao(p){
    if(!p||!p.texto)return;
    var bar=$('#fatoBar');if(!bar)return;
    bar.textContent='';bar.hidden=false;
    bar.append(h('span',{class:'ft',text:'💡 '+t('factQ')+' “'+p.texto+'”'}));
    var s=btn(t('factSave'),'baixar',async function(){
      s.disabled=true;
      try{var r=await fetch('/api/fatos/confirmar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({texto:p.texto})});
        var o=await r.json();toast(r.ok?(o.novo?t('factSaved'):t('factDup')):t('factErr'),r.ok?'ok':'err');
      }catch(e){toast(t('factErr'),'err');}
      bar.hidden=true;});
    var d=btn(t('factDiscard'),'baixar',function(){bar.hidden=true;});
    bar.append(s,d);
  }
};

function init(host){
  H=host;
  document.querySelectorAll('.sec .rest').forEach(function(b){b.onclick=function(){restaurar(b.dataset.rest);};});
  var cb=$('#ctxBtn');if(cb)cb.onclick=CH.abrirContexto;
  var ps=$('#projSel');if(ps)ps.onchange=function(){CH.trocarProjeto(ps.value);};
}
window.AjustesUI={init:init,setLang:function(l){LANG=l;},titulos:titulos,render:render,t:t,chat:CH,_D:D};
})();
