const e=id=>document.getElementById(id);

function formatMessage(value){
  if(typeof value==='string') return value;
  try{return JSON.stringify(value,null,2);}catch{return String(value);}
}

function detectLevel(text,label){
  const lower=text.toLowerCase();
  if(label){
    const labelLower=label.toLowerCase();
    if(labelLower.includes('error')||labelLower.includes('fail')) return 'error';
    if(labelLower.includes('warn')) return 'warn';
    if(labelLower.includes('ok')||labelLower.includes('done')||labelLower.includes('success')) return 'success';
  }
  if(lower.includes('error')||lower.includes('fail')||lower.includes('not ready')) return 'error';
  if(lower.includes('warn')||lower.includes('delay')||lower.includes('risk')) return 'warn';
  if(lower.includes('ok')||lower.includes('готов')||lower.includes('success')||lower.includes('ready')) return 'success';
  return 'info';
}

function ap(raw){
  const container=e('log');
  if(!container) return;

  const message=formatMessage(raw);
  const match=/^\s*\[([^\]]+)\]\s*(.*)$/.exec(message);
  const label=match?match[1]:'info';
  const text=match?match[2]:message;
  const level=detectLevel(text,label);

  const time=new Date().toLocaleTimeString('ru-RU',{hour12:false});
  const entry=document.createElement('div');
  entry.className=`log-entry ${level}`;

  const timeEl=document.createElement('span');
  timeEl.className='log-time';
  timeEl.textContent=time;

  const textWrap=document.createElement('div');
  const labelEl=document.createElement('span');
  labelEl.className='log-label';
  labelEl.textContent=label;
  const textEl=document.createElement('div');
  textEl.className='log-text';
  textEl.textContent=text || label;

  textWrap.appendChild(labelEl);
  textWrap.appendChild(textEl);

  entry.appendChild(timeEl);
  entry.appendChild(textWrap);

  container.appendChild(entry);
  container.scrollTop=container.scrollHeight;
}
async function jget(u){const r=await fetch(u); return await r.json()}
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}); return await r.json()}

async function load(){ const s=await jget('/api/settings'); const S=s.settings||{};
  e('addr').textContent=S.ADDRESS||'—'; e('chain').textContent=S.CHAIN||'—';
  e('mode').textContent=S.MODE||'—'; e('riskCur').textContent=S.RISK_PROFILE||'—'; e('live').textContent=(S.OPENSEA_API_KEY?'ready':'not ready');
  e('chainSel').value=S.CHAIN||'eth'; try{ e('contracts').value=JSON.stringify(JSON.parse(S.CONTRACTS||'[]'),null,2)}catch{ e('contracts').value=S.CONTRACTS||'[]' }
  e('osKey').value=S.OPENSEA_API_KEY||''; e('modeSel').value=S.MODE||'paper';
  e('balSrc').value=S.BALANCE_SOURCE||'auto';
}

async function wallet(){ const w=await jget('/api/wallet');
  e('bal').textContent=(w.eth||0).toFixed(6); e('usd').textContent=(w.usd==null?'—':w.usd.toFixed(2));
  if(w.source){ e('balSrcBadge').textContent='src: '+w.source; } }

async function kpi(){ const j=await jget('/api/kpi'); const m=j.kpi||{};
  function upd(key, barId, wrId){ const v=m[key]||{winrate:0}; e(wrId).textContent=(v.winrate||0)+'%'; e(barId).style.width=(v.winrate||0)+'%'}
  upd('undercut','bar_u','wr_u'); upd('mean_revert','bar_mr','wr_mr'); upd('momentum','bar_m','wr_m'); upd('hybrid','bar_h','wr_h')
}
async function leader(){ const j=await jget('/api/leader'); const L=j.leader||{};
  e('nl').textContent=L.nl||e('nl').textContent; e('best').textContent=L.best||'—'
}

e('rpc').onclick=async()=>{ap('[UI] rpc'); ap(await jpost('/api/rpc_check',{}))}
e('test').onclick=async()=>{ap('[UI] test'); ap(await jget('/api/test'))}
e('modeSave').onclick=async()=>{ap('[UI] mode'); ap(await jpost('/api/mode_set',{MODE:e('modeSel').value}))}
e('start').onclick=async()=>{ap('[UI] start'); ap(await jpost('/api/start',{}))}
e('stop').onclick=async()=>{ap('[UI] stop'); ap(await jpost('/api/stop',{}))}
e('osSave').onclick=async()=>{ap('[UI] osSave'); ap(await jpost('/api/opensea_set',{OPENSEA_API_KEY:e('osKey').value}))}
e('chainSave').onclick=async()=>{ap('[UI] chain'); ap(await jpost('/api/chain_set',{chain:e('chainSel').value}))}
e('applyContracts').onclick=async()=>{ap('[UI] patch'); ap(await jpost('/api/patch',{CONTRACTS:JSON.parse(e('contracts').value||'[]')}))}
e('balSrcSave').onclick=async()=>{ap('[UI] balSrc'); ap(await jpost('/api/balance_source_set',{source:e('balSrc').value}))}

async function boot(){ ap('[UI] boot'); ap(await jget('/api/js-ok')); ap(await jget('/api/ping')); ap(await jget('/api/test')); await load(); await wallet(); await kpi(); await leader() }
boot(); setInterval(async()=>{await wallet(); await kpi(); await leader()}, 4000)

e('riskSave').onclick=async()=>{
  const v=e('riskSel').value;
  ap('[UI] risk profile -> '+v);
  ap(await jpost('/api/risk_mode_set',{profile:v}));
  await load();
}
e('riskSel').onchange=()=>{
  const v=e('riskSel').value;
  const D={
    conservative:'Минимальные риски: меньший размер позиции, более высокий минимум профита, ниже лимит газа, редче запросы Moralis.',
    balanced:'Сбалансировано: стандартные лимиты, средний размер позиции.',
    aggressive:'Выше риск: крупнее позиции, ниже целевой профит на сделку, выше лимит газа, быстрее реакции.'
  };
  e('riskDesc').textContent=D[v]||'';
}
