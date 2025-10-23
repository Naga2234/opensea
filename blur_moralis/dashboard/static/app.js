const e=id=>document.getElementById(id);
const REFRESH_INTERVAL=4000;
const STATUS_INTERVAL=2500;
const LOG_INTERVAL=2500;
const USAGE_INTERVAL=15000;
const MAX_LOG_ENTRIES=400;
const LOG_SCROLL_THRESHOLD=32;
let engineStatusTimer=null;
let logTimer=null;
let usageTimer=null;
let logCursor=0;
let logAutoScroll=true;
let lastUsageHash=null;
let lastUsageLogTs=0;
let strategyStatus=null;
let currentChain=null;

const CHAIN_SYMBOLS={
  eth:'ETH',
  ethereum:'ETH',
  polygon:'MATIC',
  matic:'MATIC'
};

function chainSymbol(chain){
  if(!chain) return '';
  const key=String(chain).toLowerCase();
  return CHAIN_SYMBOLS[key]||String(chain).toUpperCase();
}

function setLoading(button, loading){
  if(!button) return;
  button.disabled=!!loading;
  if(loading){
    button.dataset.loading='true';
    button.setAttribute('aria-busy','true');
  }else{
    delete button.dataset.loading;
    button.removeAttribute('aria-busy');
  }
}

function setupAction(buttonId, action, {logLabel}={}){
  const button=e(buttonId);
  if(!button) return;
  button.addEventListener('click',async()=>{
    const label=logLabel||buttonId;
    ap(`[UI] ${label}`);
    try{
      setLoading(button,true);
      const result=await action();
      if(result!==undefined) ap(result);
    }catch(err){
      ap(`[${label}] error: ${(err?.message)||err}`);
    }finally{
      setLoading(button,false);
    }
  });
}

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

function isNearLogBottom(container){
  if(!container) return true;
  const distance=container.scrollHeight-container.scrollTop-container.clientHeight;
  return distance<=LOG_SCROLL_THRESHOLD;
}

function updateLogScrollState(container){
  if(!container) return;
  const sticky=isNearLogBottom(container);
  logAutoScroll=sticky;
  if(sticky){
    delete container.dataset.paused;
    container.removeAttribute('data-paused');
  }else{
    container.dataset.paused='true';
  }
}

function setupLogScrollHandling(){
  const container=e('log');
  if(!container) return;
  updateLogScrollState(container);
  container.addEventListener('scroll',()=>{
    updateLogScrollState(container);
  });
}

function ap(raw){
  const container=e('log');
  if(!container) return;

  const message=formatMessage(raw);
  const match=/^\s*\[([^\]]+)\]\s*(.*)$/.exec(message);
  let label=match?match[1]:'info';
  let text=match?match[2]:message;
  const nested=/^\s*\[([^\]]+)\]\s*(.*)$/.exec(text);
  if(nested){
    label=nested[1];
    text=nested[2]||nested[1];
  }
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
  delete container.dataset.empty;
  container.removeAttribute('data-empty');
  if(container.childElementCount>MAX_LOG_ENTRIES){
    const first=container.firstElementChild;
    if(first) container.removeChild(first);
  }
  if(logAutoScroll||isNearLogBottom(container)){
    container.scrollTop=container.scrollHeight;
    logAutoScroll=true;
    delete container.dataset.paused;
    container.removeAttribute('data-paused');
  }
}

function formatDuration(seconds){
  if(seconds==null) return null;
  const num=Number(seconds);
  if(!Number.isFinite(num)) return null;
  const total=Math.max(0,Math.floor(num));
  const hours=Math.floor(total/3600);
  const minutes=Math.floor((total%3600)/60);
  const secs=total%60;
  const parts=[];
  if(hours) parts.push(hours+'ч');
  if(minutes) parts.push(minutes+'м');
  if(!hours && (minutes<3)) parts.push(secs+'с');
  if(!parts.length) parts.push('0с');
  return parts.join(' ');
}

function formatAgo(seconds){
  if(seconds==null) return null;
  const num=Number(seconds);
  if(!Number.isFinite(num)) return null;
  const formatted=formatDuration(num);
  return formatted?formatted+' назад':null;
}

function formatTimestamp(ts){
  if(ts==null) return null;
  const num=Number(ts);
  if(!Number.isFinite(num) || num<=0) return null;
  try{
    return new Date(num*1000).toLocaleString('ru-RU',{hour12:false});
  }catch{return null;}
}

function formatDateLike(value){
  if(value==null) return null;
  if(typeof value==='number'){ return formatTimestamp(value); }
  const date=new Date(value);
  if(Number.isNaN(date.getTime())) return null;
  return date.toLocaleString('ru-RU',{hour12:false});
}

const tradeStateLabels={
  starting:'Запуск движка',
  idle:'Нет сделок',
  waiting:'Ожидаем сигнал',
  scanning:'Сканируем',
  signal:'Сигнал найден',
  entering:'Вход в сделку',
  filled:'Заявка отправлена',
  win:'Сделка прибыльная',
  loss:'Сделка убыточная',
  skipped:'Пропуск',
  error:'Ошибка сделки',
};

const tradeProgressMap={
  starting:12,
  idle:8,
  waiting:18,
  scanning:28,
  signal:45,
  entering:65,
  filled:85,
  win:100,
  loss:100,
  skipped:12,
  error:85,
};

const strategyDisplayNames={
  auto:'Авто (переключение)',
  undercut:'undercut',
  mean_revert:'mean_revert',
  momentum:'momentum',
  hybrid:'hybrid',
};

function formatStrategyName(value){
  return strategyDisplayNames[value]||value||'—';
}

function updateStrategyHint(status){
  const hintEl=e('strategyHint');
  if(!hintEl) return;
  const mode=(status?.mode==='manual')?'manual':'auto';
  const manual=status?.manual;
  if(mode==='manual' && manual){
    hintEl.textContent=`Ручной режим: используется стратегия ${formatStrategyName(manual)}.`;
  }else if(mode==='manual'){
    hintEl.textContent='Ручной режим: стратегия не выбрана.';
  }else{
    hintEl.textContent='Авто: движок сам выбирает подходящую стратегию.';
  }
}

async function loadStrategy(){
  try{
    const response=await jget('/api/strategy_status');
    strategyStatus=response?.strategy||{};
    const select=e('strategySel');
    if(select){
      const mode=strategyStatus.mode==='manual'?'manual':'auto';
      const manual=strategyStatus.manual;
      const value=(mode==='manual' && manual)?manual:'auto';
      select.value=value;
    }
    updateStrategyHint(strategyStatus);
    return strategyStatus;
  }catch(err){
    ap('[strategy] '+(err?.message||err));
    updateStrategyHint(strategyStatus);
    return null;
  }
}

function setTradeStatusUI(trade){
  const container=e('tradeStatus');
  if(!container) return;
  const progress=e('tradeStatusProgress');
  const badge=e('tradeStatusBadge');
  const meta=e('tradeStatusMeta');
  const state=(trade?.status)||'idle';
  container.dataset.state=state;
  if(badge){ badge.textContent=tradeStateLabels[state]||state||'—'; }
  if(progress){
    const width=tradeProgressMap[state]||8;
    progress.style.width=width+'%';
  }
  if(meta){
    const parts=[];
    if(trade?.strategy) parts.push('Стратегия '+trade.strategy);
    if(typeof trade?.contract==='string' && trade.contract) parts.push('Контракт '+trade.contract.slice(0,8)+'…');
    const symbol=trade?.symbol||chainSymbol(trade?.chain||currentChain);
    const sizeNativeRaw=Number(trade?.size_native);
    if(Number.isFinite(sizeNativeRaw) && sizeNativeRaw>0){
      const sizeNative=sizeNativeRaw>=0.01?sizeNativeRaw.toFixed(2):sizeNativeRaw.toFixed(4);
      parts.push('Объём ≈'+sizeNative+(symbol?' '+symbol:''));
    }
    const pnlNativeRaw=Number(trade?.pnl_native);
    if(Number.isFinite(pnlNativeRaw) && pnlNativeRaw!==0){
      const sign=pnlNativeRaw>0?'+':'';
      const magnitude=Math.abs(pnlNativeRaw);
      const formatted=magnitude>=0.01?magnitude.toFixed(2):magnitude.toFixed(4);
      parts.push('PnL '+sign+formatted+(symbol?' '+symbol:''));
    }
    if(trade?.note) parts.push(trade.note);
    if(trade?.ts){
      const ago=formatAgo((Date.now()/1000)-Number(trade.ts));
      if(ago) parts.push('Обновлено '+ago);
    }
    meta.textContent=parts.length?parts.join(' · '):'Ожидаем сигнал';
  }
}

function summarizeUsage(usage){
  if(!usage) return 'нет данных';
  const current=Number(usage.current);
  const limit=Number(usage.limit);
  const remaining=usage.remaining!=null?Number(usage.remaining):null;
  const parts=[];
  if(Number.isFinite(current) && Number.isFinite(limit)) parts.push(current.toFixed(2)+' / '+limit.toFixed(2)+' CU');
  else if(Number.isFinite(current)) parts.push(current.toFixed(2)+' CU');
  if(Number.isFinite(remaining)) parts.push('осталось '+remaining.toFixed(2)+' CU');
  return parts.join(' · ')||'нет данных';
}

function setCuUsageUI(usage){
  const container=e('cuUsageProgress');
  if(!container) return;
  const labelEl=e('cuUsageLabel');
  const remainingEl=e('cuUsageRemaining');
  const metaEl=e('cuUsageMeta');
  const current=Number(usage?.current);
  const limit=Number(usage?.limit);
  const remaining=usage?.remaining!=null?Number(usage.remaining):null;
  const pct=Number.isFinite(current) && Number.isFinite(limit) && limit>0?Math.min(100,Math.max(0,(current/limit)*100)):0;
  container.style.width=pct+'%';
  const meterWrap=container.parentElement?.parentElement;
  const usageHolder=meterWrap?meterWrap.parentElement:null;
  if(usageHolder) usageHolder.dataset.state=pct>=90?'critical':(pct>=70?'warn':'ok');
  if(labelEl){
    if(Number.isFinite(current) && Number.isFinite(limit)) labelEl.textContent=current.toFixed(2)+' / '+limit.toFixed(2)+' CU';
    else if(Number.isFinite(current)) labelEl.textContent=current.toFixed(2)+' CU';
    else labelEl.textContent='—';
  }
  if(remainingEl){
    if(Number.isFinite(remaining)) remainingEl.textContent='осталось '+remaining.toFixed(2)+' CU';
    else remainingEl.textContent='—';
  }
  if(metaEl){
    const metaParts=[];
    const period=usage?.period||usage?.window;
    const reset=formatDateLike(usage?.reset_at);
    if(period) metaParts.push(String(period));
    if(reset) metaParts.push('Сброс '+reset);
    if(usage?.fetched_at){
      const ago=formatAgo((Date.now()/1000)-Number(usage.fetched_at));
      if(ago) metaParts.push('Обновлено '+ago);
    }
    metaEl.textContent=metaParts.length?metaParts.join(' · '):'Нет данных';
  }
}

function setEngineStatusUI(status){
  const container=e('engineStatus');
  const textEl=e('engineStatusText');
  const metaEl=e('engineStatusMeta');
  if(!container||!textEl||!metaEl) return;
  const state=status?.state||(status?.running?'running':status?.stopping?'stopping':(status?'idle':'unknown'));
  container.dataset.state=state||'unknown';
  if(state==='running'||state==='stopping') container.setAttribute('aria-busy','true');
  else container.removeAttribute('aria-busy');
  if(status?.running) container.dataset.running='true'; else delete container.dataset.running;
  if(status?.stopping) container.dataset.stopping='true'; else delete container.dataset.stopping;
  let label='—';
  const info=[];
  if(state==='running'){
    label='Работает';
    const uptime=formatDuration(status?.uptime);
    if(uptime) info.push('Аптайм '+uptime);
  }else if(state==='stopping'){
    label='Останавливается';
    if(status?.stop_reason) info.push(status.stop_reason);
    else info.push('Ждём завершения операций');
  }else if(state==='idle'){
    label='Остановлен';
  }else{
    label='Нет данных';
  }
  if(state==='idle' && status?.stop_reason){
    info.push(status.stop_reason);
  }
  if(state==='idle' && !status?.stop_reason){
    const stopped=formatTimestamp(status?.stopped_at);
    if(stopped) info.push('Остановлен '+stopped);
  }
  if(status?.heartbeat_ago!=null){
    const ago=formatAgo(status.heartbeat_ago);
    if(ago) info.push('Пульс '+ago);
  }
  if(status?.last_trade){
    const trade=status.last_trade;
    const stateLabel=tradeStateLabels[trade.status]||null;
    if(stateLabel){
      const contractLabel=typeof trade?.contract==='string' && trade.contract?trade.contract.slice(0,8)+'…':'';
      const note=trade?.note||'';
      const segments=[stateLabel.toLowerCase(), contractLabel, note].filter(Boolean);
      if(segments.length) info.push('Сделка: '+segments.join(' '));
    }
  }
  if(status?.strategy){
    const strat=status.strategy;
    if(strat.mode==='manual' && strat.manual){
      info.push('Ручная стратегия: '+formatStrategyName(strat.manual));
    }else if(strat.mode==='auto'){
      info.push('Стратегии: авто');
    }
  }
  if(state==='idle' && !info.length){
    info.push('Готов к запуску');
  }
  textEl.textContent=label;
  metaEl.textContent=info.length?info.join(' · '):'Нет данных';
  setTradeStatusUI(status?.last_trade);
}

async function engineStatus(){
  try{
    const response=await jget('/api/status');
    setEngineStatusUI(response.status);
    return response;
  }catch(err){
    setEngineStatusUI(null);
    ap('[status] '+(err?.message||err));
    return null;
  }
}

function startEngineStatusPolling(){
  if(engineStatusTimer) clearInterval(engineStatusTimer);
  engineStatusTimer=setInterval(engineStatus, STATUS_INTERVAL);
}

async function fetchLogs(){
  try{
    const response=await jget(`/api/logs?since=${logCursor}`);
    const logs=response?.logs||[];
    logs.forEach(entry=>{
      if(entry?.id!=null) logCursor=Math.max(logCursor, Number(entry.id));
      if(entry?.line) ap(entry.line);
    });
  }catch(err){
    ap('[logs] '+(err?.message||err));
  }
}

function startLogPolling(){
  if(logTimer) clearInterval(logTimer);
  fetchLogs();
  logTimer=setInterval(fetchLogs, LOG_INTERVAL);
}

async function moralisUsage(){
  try{
    const response=await jget('/api/moralis_usage');
    const usage=response?.usage||null;
    setCuUsageUI(usage);
    if(usage){
      const hash=[usage.current,usage.limit,usage.remaining,usage.period,usage.reset_at].join('|');
      const now=Date.now();
      if(hash!==lastUsageHash || (now-lastUsageLogTs)>=60000){
        lastUsageHash=hash;
        lastUsageLogTs=now;
        ap('[MORALIS][USAGE] '+summarizeUsage(usage));
      }
    }
    return usage;
  }catch(err){
    ap('[usage] '+(err?.message||err));
    return null;
  }
}

function startUsagePolling(){
  if(usageTimer) clearInterval(usageTimer);
  moralisUsage();
  usageTimer=setInterval(moralisUsage, USAGE_INTERVAL);
}

async function jget(u){const r=await fetch(u); return await r.json()}
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}); return await r.json()}

function describeRisk(profile){
  const D={
    conservative:'Минимальные риски: меньший размер позиции, более высокий минимум профита, ниже лимит газа, реже запросы Moralis.',
    balanced:'Сбалансировано: стандартные лимиты, средний размер позиции.',
    aggressive:'Выше риск: крупнее позиции, ниже целевой профит на сделку, выше лимит газа, быстрее реакции.'
  };
  return D[profile]||'';
}

function setRiskProfileUI(value){
  if(!value) return;
  const control=e('riskSel');
  control.value=value;
  e('riskDesc').textContent=describeRisk(value);
}

async function load(){ const s=await jget('/api/settings'); const S=s.settings||{};
  currentChain=S.CHAIN||null;
  e('addr').textContent=S.ADDRESS||'—'; e('chain').textContent=S.CHAIN||'—';
  e('mode').textContent=S.MODE||'—'; e('riskCur').textContent=S.RISK_PROFILE||'—'; e('live').textContent=(S.OPENSEA_API_KEY?'ready':'not ready');
  e('chainSel').value=S.CHAIN||'eth'; try{ e('contracts').value=JSON.stringify(JSON.parse(S.CONTRACTS||'[]'),null,2)}catch{ e('contracts').value=S.CONTRACTS||'[]' }
  e('osKey').value=S.OPENSEA_API_KEY||''; e('modeSel').value=S.MODE||'paper';
  e('balSrc').value=S.BALANCE_SOURCE||'auto';
  if(S.RISK_PROFILE) setRiskProfileUI(S.RISK_PROFILE);
}

async function wallet(){ const w=await jget('/api/wallet');
  const amountRaw=Number(w.balance ?? w.eth ?? 0);
  const amount=Number.isFinite(amountRaw)?amountRaw:0;
  const symbol=w.symbol||chainSymbol(w.chain||currentChain);
  e('bal').textContent=Number.isFinite(amountRaw)?amount.toFixed(6):'—';
  const symbolEl=e('balSymbol');
  if(symbolEl){ symbolEl.textContent=symbol||''; symbolEl.style.display=symbol?'inline-block':'none'; }
  const srcEl=e('balSrcBadge');
  if(srcEl){
    if(w.source){ srcEl.textContent='src: '+w.source; srcEl.style.display='inline-block'; }
    else{ srcEl.textContent=''; srcEl.style.display='none'; }
  }
  const collectionEl=e('collectionCount');
  if(collectionEl){
    let count=null;
    if(Number.isFinite(Number(w.collection_count))){ count=Number(w.collection_count); }
    else if(Array.isArray(w.collection)){ count=w.collection.length; }
    collectionEl.textContent=(count!=null && Number.isFinite(count))?count:'—';
  }
}

async function kpi(){ const j=await jget('/api/kpi'); const m=j.kpi||{};
  function upd(key, barId, wrId){ const v=m[key]||{winrate:0}; e(wrId).textContent=(v.winrate||0)+'%'; e(barId).style.width=(v.winrate||0)+'%'}
  upd('undercut','bar_u','wr_u'); upd('mean_revert','bar_mr','wr_mr'); upd('momentum','bar_m','wr_m'); upd('hybrid','bar_h','wr_h')
}
async function leader(){ const j=await jget('/api/leader'); const L=j.leader||{};
  e('nl').textContent=L.nl||e('nl').textContent; e('best').textContent=L.best||'—'
}

const riskProfileNames={
  conservative:'Консервативный',
  balanced:'Сбалансированный',
  aggressive:'Агрессивный'
};

function renderRiskStatsRow(profile,data,best){
  const tr=document.createElement('tr');
  tr.dataset.profile=profile;
  if(profile===best) tr.classList.add('risk-best');
  const fmt=(value,def='—')=>value==null?def:value;
  const labels=['Профиль','Сделок','Win‑rate','Avg PnL','Общая прибыль'];
  const cells=[
    riskProfileNames[profile]||profile,
    fmt(data?.trades),
    data?.winrate!=null?(data.winrate.toFixed?Number(data.winrate).toFixed(1):data.winrate)+'%':'—',
    data?.avgProfit!=null?Number(data.avgProfit).toFixed(2):'—',
    data?.totalProfit!=null?Number(data.totalProfit).toFixed(2):'—'
  ];
  cells.forEach((text,index)=>{
    const td=document.createElement('td');
    td.textContent=text;
    td.dataset.label=labels[index];
    tr.appendChild(td);
  });
  return tr;
}

function inferBestProfile(stats){
  let best=null; let bestProfit=-Infinity;
  Object.entries(stats||{}).forEach(([profile,data])=>{
    const profit=Number(data?.totalProfit);
    if(!Number.isFinite(profit)) return;
    if(profit>bestProfit){
      bestProfit=profit;
      best=profile;
    }
  });
  return best;
}

async function riskStats(){
  const container=e('riskStatsBody');
  if(!container) return;
  try{
    const response=await jget('/api/risk_stats');
    const stats=response.stats||{};
    const best=response.best||inferBestProfile(stats)||null;
    container.innerHTML='';
    const profiles=['conservative','balanced','aggressive'];
    profiles.forEach(profile=>{
      const row=renderRiskStatsRow(profile,stats[profile],best);
      container.appendChild(row);
    });
    const highlight=e('riskStatsHighlight');
    if(highlight){
      highlight.textContent=best?(riskProfileNames[best]||best):'—';
    }
  }catch(err){
    ap('[risk_stats] '+(err?.message||err));
  }
}

function normalizeContracts(value){
  const trimmed=(value||'').trim();
  if(!trimmed) return [];
  try{
    const parsed=JSON.parse(trimmed);
    if(Array.isArray(parsed)){
      const cleaned=parsed.map(item=>typeof item==='string'?item.trim():item).filter(Boolean);
      return [...new Set(cleaned)];
    }
    if(typeof parsed==='string'){
      const single=parsed.trim();
      return single?[single]:[];
    }
  }catch(err){
    const tokens=trimmed.split(/[\s,]+/).map(token=>token.trim()).filter(Boolean);
    const uniqueTokens=[...new Set(tokens)];
    if(uniqueTokens.length && uniqueTokens.every(token=>/^0x[a-fA-F0-9]{40}$/.test(token))) return uniqueTokens;
  }
  throw new Error('Не удалось распознать список контрактов. Используйте JSON-массив или список адресов через пробел/перенос строки.');
}

setupAction('rpc',()=>jpost('/api/rpc_check',{}),{logLabel:'rpc'});
setupAction('test',()=>jget('/api/test'),{logLabel:'test'});
setupAction('modeSave',()=>jpost('/api/mode_set',{MODE:e('modeSel').value}),{logLabel:'mode'});
setupAction('start',async()=>{
  setEngineStatusUI({state:'running',running:true,uptime:0,heartbeat_ago:0});
  try{
    const response=await jpost('/api/start',{});
    if(response?.status) setEngineStatusUI(response.status); else await engineStatus();
    startEngineStatusPolling();
    return response;
  }catch(err){
    await engineStatus();
    throw err;
  }
},{logLabel:'start'});
setupAction('stop',async()=>{
  setEngineStatusUI({state:'stopping',stopping:true});
  try{
    const response=await jpost('/api/stop',{});
    if(response?.status) setEngineStatusUI(response.status); else await engineStatus();
    startEngineStatusPolling();
    return response;
  }catch(err){
    await engineStatus();
    throw err;
  }
},{logLabel:'stop'});
setupAction('osSave',()=>jpost('/api/opensea_set',{OPENSEA_API_KEY:e('osKey').value}),{logLabel:'os key'});
setupAction('chainSave',()=>jpost('/api/chain_set',{chain:e('chainSel').value}),{logLabel:'chain'});
setupAction('balSrcSave',()=>jpost('/api/balance_source_set',{source:e('balSrc').value}),{logLabel:'balance source'});
setupAction('applyContracts',async()=>{
  const contracts=normalizeContracts(e('contracts').value||'');
  ap(`[contracts] подготовлено ${contracts.length}`);
  const response=await jpost('/api/patch',{CONTRACTS:contracts});
  await load();
  return response;
},{logLabel:'contracts'});
setupAction('strategySave',async()=>{
  const select=e('strategySel');
  const value=select?select.value:'auto';
  const body=value==='auto'?{mode:'auto'}:{mode:'manual',strategy:value};
  const response=await jpost('/api/strategy_set',body);
  await loadStrategy();
  return response;
},{logLabel:'strategy'});
setupAction('riskSave',async()=>{
  const profile=e('riskSel').value;
  const readable=riskProfileNames[profile]||profile;
  ap('[UI] risk profile -> '+readable);
  const response=await jpost('/api/risk_mode_set',{profile});
  await load();
  return response;
},{logLabel:'risk profile'});

async function boot(){
  ap('[UI] boot');
  ap(await jget('/api/js-ok'));
  ap(await jget('/api/ping'));
  ap(await jget('/api/test'));
  await load();
  await loadStrategy();
  await wallet();
  await kpi();
  await leader();
  await riskStats();
  await engineStatus();
  startEngineStatusPolling();
  startLogPolling();
  startUsagePolling();
}
async function refresh(){ await wallet(); await kpi(); await leader(); await riskStats(); await loadStrategy() }
setupLogScrollHandling();
boot(); setInterval(refresh, REFRESH_INTERVAL)

e('riskSel').onchange=()=>{ const v=e('riskSel').value; e('riskDesc').textContent=describeRisk(v)}
setRiskProfileUI(e('riskSel').value);
const strategySelEl=e('strategySel');
if(strategySelEl){
  strategySelEl.addEventListener('change',()=>{
    const value=strategySelEl.value;
    const preview=value==='auto'?{mode:'auto'}:{mode:'manual',manual:value};
    updateStrategyHint(preview);
  });
}
