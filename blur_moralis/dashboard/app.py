from fastapi import FastAPI, Body
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from ..runtime import log, get_logs
from ..config import (
    settings,
    contracts,
    rpc_urls,
    available_strategies,
    normalize_strategy,
    strategy_state,
)
from ..pricing import price_usd
from ..executor import Web3Helper, LiveNotConfigured
from ..engine import Engine
from ..moralis_api import native_balance, ping as moralis_ping, current_cu_usage
import os, json, time

app = FastAPI()
log("[BOOT] dashboard app loaded — Moralis integrated; open /")

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", response_class=HTMLResponse)
def index(): return FileResponse(os.path.join(static_dir, "index.html"))

@app.get("/api/js-ok")
def api_jsok(): return {"ok":True, "msg":"JS can talk to backend"}

@app.get("/api/ping")
def api_ping():
    addr=settings.ADDRESS or ""
    live_ready = bool(settings.OPENSEA_API_KEY and settings.PRIVATE_KEY and addr)
    return {"ok":True, "mode": settings.MODE, "chain": settings.CHAIN, "address": addr, "live_ready": live_ready}

@app.get("/api/test")
def api_test():
    urls=[settings.RPC_URL]+(rpc_urls() or [])
    ok=False; cid=None
    for u in [x for x in urls if x]:
        try:
            h=Web3Helper(u)
            if h.is_ok(): ok=True; cid=h.w3.eth.chain_id; break
        except: pass
    mp=moralis_ping()
    log(f"[TEST] rpc_ok={ok} chain={settings.CHAIN} mode={settings.MODE} addr={settings.ADDRESS[:8]}… key(OS)={'yes' if settings.OPENSEA_API_KEY else 'no'} moralis={'ok' if mp else 'fail/limited'}")
    return {"ok":True, "rpc":{"connected":ok,"chain_id":cid}, "moralis": mp}

@app.post("/api/rpc_check")
def api_rpc_check(body: dict = Body(...)):
    url=(body or {}).get("url") or settings.RPC_URL
    h=Web3Helper(url)
    ok=h.is_ok()
    info={}
    if ok:
        try: info["chain_id"]=h.w3.eth.chain_id; info["latest_block"]=h.w3.eth.block_number
        except: pass
    log(f"[RPC] {url} -> ok={ok} {info}"); return {"connected": ok, **info, "rpc_url": url}

@app.get("/api/wallet")
def api_wallet():
    eth=0.0; src="rpc"; used=None
    if settings.BALANCE_SOURCE in ("rpc","auto"):
        urls=[settings.RPC_URL]+(rpc_urls() or [])
        bwei=0
        for u in [x for x in urls if x]:
            try:
                h=Web3Helper(u)
                if h.is_ok(): bwei=h.balance(settings.ADDRESS); used=u; break
            except: pass
        eth = bwei/1e18 if bwei else 0.0
        if settings.BALANCE_SOURCE=="auto" and eth==0.0:
            bwei_m = native_balance(settings.ADDRESS) or 0
            if bwei_m:
                eth = bwei_m/1e18; src="moralis"
    elif settings.BALANCE_SOURCE=="moralis":
        bwei_m = native_balance(settings.ADDRESS) or 0
        eth = bwei_m/1e18 if bwei_m else 0.0
        src="moralis"
    px = price_usd(settings.CHAIN) or 0.0
    usd = eth*px if px else None
    return {"ts": time.time(), "eth": eth, "usd": usd, "address": settings.ADDRESS, "rpc": used, "source": src}

@app.post("/api/balance_source_set")
def api_balance_source_set(body: dict = Body(...)):
    src=(body or {}).get("source","auto")
    if src not in ("auto","rpc","moralis"): return JSONResponse({"ok":False,"error":"bad source"}, status_code=400)
    _save_env({"BALANCE_SOURCE": src}); settings.BALANCE_SOURCE=src; log(f"[BALANCE] source -> {src}"); return {"ok":True, "source": src}

@app.post("/api/opensea_set")
def api_opensea_set(body: dict = Body(...)):
    key=(body or {}).get("OPENSEA_API_KEY","").strip()
    _save_env({"OPENSEA_API_KEY": key}); settings.OPENSEA_API_KEY=key
    log("[LIVE] OpenSea key updated" if key else "[LIVE] OpenSea key cleared"); return {"ok":True, "saved": bool(key)}

@app.post("/api/mode_set")
def api_mode_set(body: dict = Body(...)):
    m=(body or {}).get("MODE","paper")
    if m not in ("paper","live","auto"): return JSONResponse({"ok":False,"error":"bad mode"}, status_code=400)
    _save_env({"MODE": m}); settings.MODE=m; log(f"[MODE] {m}"); return {"ok":True, "mode": m}


@app.get("/api/strategy_status")
def api_strategy_status():
    return {"ok": True, "strategy": strategy_state()}


@app.post("/api/strategy_set")
def api_strategy_set(body: dict = Body(...)):
    payload = body or {}
    mode = (payload.get("mode") or payload.get("MODE") or settings.STRATEGY_MODE or "auto").lower()
    if mode not in ("auto", "manual"):
        return JSONResponse({"ok": False, "error": "bad mode"}, status_code=400)
    manual_raw = payload.get("strategy") or payload.get("manual") or payload.get("MANUAL_STRATEGY")
    manual = normalize_strategy(manual_raw if manual_raw is not None else settings.MANUAL_STRATEGY)
    updates = {"STRATEGY_MODE": mode}
    settings.STRATEGY_MODE = mode
    if mode == "manual":
        if not manual:
            return JSONResponse({"ok": False, "error": "manual strategy required"}, status_code=400)
        updates["MANUAL_STRATEGY"] = manual
        settings.MANUAL_STRATEGY = manual
        log(f"[STRATEGY] ручной режим: {manual}")
    else:
        if manual:
            updates["MANUAL_STRATEGY"] = manual
            settings.MANUAL_STRATEGY = manual
        log("[STRATEGY] режим: авто")
    _save_env(updates)
    return {"ok": True, "strategy": strategy_state(), "available": available_strategies()}

@app.post("/api/chain_set")
def api_chain_set(body: dict = Body(...)):
    chain=(body or {}).get("chain","eth").lower()
    if chain not in ("eth","ethereum","polygon","matic"): return JSONResponse({"ok":False,"error":"bad chain"}, status_code=400)
    if chain in ("ethereum","eth"): chain="eth"
    if chain=="matic": chain="polygon"
    _save_env({"CHAIN": chain}); settings.CHAIN = chain; log(f"[CHAIN] set -> {chain}"); return {"ok": True, "chain": chain}

@app.post("/api/preset_lowcap_polygon")
def api_preset_lowcap_polygon():
    pairs={
        "CHAIN":"polygon",
        "RPC_URL":"https://polygon-rpc.com",
        "RPC_URLS": '["https://polygon-rpc.com","https://rpc.ankr.com/polygon"]',
        "POSITION_FRACTION":"0.002",
        "POSITION_USD_CEIL":"3",
        "MAX_SPEND_USD_PER_DAY":"6",
        "MAX_OPEN_POSITIONS":"1",
        "USD_PROFIT_MIN":"0.01",
        "GAS_MAX_FEE_GWEI":"60",
        "GAS_PRIORITY_GWEI":"1.5",
        "RISK_PROFILE":"lowcap_polygon",
        "CONTRACTS": '["0x67F4732266C7300cca593c814d46bee72e40659F","0x2b4a66557a79263275826ad31a4cddc2789334bd","0x86935F11C86623deC8a25696E1C19a8659CbF95d"]'
    }
    for k,v in pairs.items(): setattr(settings, k, v)
    _save_env(pairs); log("[PRESET] Low‑Cap Polygon applied"); return {"ok":True, "applied":pairs}

@app.post("/api/patch")
def api_patch(body: dict = Body(...)):
    pairs={}
    if "CONTRACTS" in (body or {}):
        pairs["CONTRACTS"]=json.dumps(body["CONTRACTS"]); settings.CONTRACTS=pairs["CONTRACTS"]
    if "RPC_URL" in (body or {}):
        pairs["RPC_URL"]=body["RPC_URL"]; settings.RPC_URL=pairs["RPC_URL"]
    if "RPC_URLS" in (body or {}):
        pairs["RPC_URLS"]=json.dumps(body["RPC_URLS"]); settings.RPC_URLS=pairs["RPC_URLS"]
    if pairs: _save_env(pairs); log(f"[PATCH] {list(pairs.keys())}")
    return {"ok":True, "applied": pairs}

@app.get("/api/kpi")
def api_kpi():
    from ..stats import kpi; return {"ok":True, "kpi": kpi()}

@app.get("/api/leader")
def api_leader():
    from ..stats import leaderboard; return {"ok":True, "leader": leaderboard()}

@app.get("/api/logs")
def api_logs(since:int=0): return {"ok":True, "logs": get_logs(since)}

@app.get("/api/moralis_usage")
def api_moralis_usage(force: bool = False):
    try:
        usage = current_cu_usage(force=force)
    except Exception as e:
        log(f"[MORALIS][ERR] usage endpoint: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return {"ok": usage is not None, "usage": usage}

@app.get("/api/settings")
def api_settings():
    return {"ok":True, "settings": {k:getattr(settings,k) for k in ["MODE","CHAIN","ADDRESS","OPENSEA_API_KEY","RPC_URL","RPC_URLS","CONTRACTS","BALANCE_SOURCE","RISK_PROFILE","STRATEGY_MODE","MANUAL_STRATEGY"]}}

@app.get("/api/status")
def api_status():
    return {"ok": True, "status": Engine().status()}

@app.post("/api/start")
def api_start():
    engine=Engine()
    try:
        engine.start()
    except LiveNotConfigured as e:
        return JSONResponse({"ok":False, "error":str(e)}, status_code=400)
    except Exception as e:
        log(f"[ENGINE][ERR] start: {e}")
        return JSONResponse({"ok":False, "error":str(e)}, status_code=400)
    return {"ok":True, "status": engine.status()}

@app.post("/api/stop")
def api_stop():
    engine=Engine(); engine.stop(); return {"ok":True, "status": engine.status()}

def _save_env(pairs:dict):
    path=".env"
    try: txt=open(path,"r",encoding="utf-8").read()
    except: txt=""
    for k,v in pairs.items():
        import re, json as _j
        vv=v if isinstance(v,str) else _j.dumps(v)
        if re.search(rf"^{k}=.*$", txt, flags=re.M):
            txt=re.sub(rf"^{k}=.*$", f"{k}={vv}", txt, flags=re.M)
        else:
            if txt and not txt.endswith("\n"): txt+="\n"
            txt+=f"{k}={vv}\n"
    open(path,"w",encoding="utf-8").write(txt)

@app.post("/api/risk_mode_set")
def api_risk_mode_set(body: dict = Body(...)):
    mode=(body or {}).get("profile","balanced").lower()
    if mode not in ("conservative","balanced","aggressive"):
        return JSONResponse({"ok":False,"error":"bad profile"}, status_code=400)
    # presets
    if mode=="conservative":
        pairs={
            "RISK_PROFILE":"conservative",
            "POSITION_FRACTION":"0.001",
            "POSITION_USD_CEIL":"2",
            "MAX_SPEND_USD_PER_DAY":"3",
            "MAX_OPEN_POSITIONS":"1",
            "USD_PROFIT_MIN":"0.02",
            "GAS_MAX_FEE_GWEI":"50",
            "GAS_PRIORITY_GWEI":"1.0",
            "MORALIS_RATE_LIMIT_SEC":"120",
        }
    elif mode=="aggressive":
        pairs={
            "RISK_PROFILE":"aggressive",
            "POSITION_FRACTION":"0.005",
            "POSITION_USD_CEIL":"5",
            "MAX_SPEND_USD_PER_DAY":"12",
            "MAX_OPEN_POSITIONS":"3",
            "USD_PROFIT_MIN":"0.005",
            "GAS_MAX_FEE_GWEI":"80",
            "GAS_PRIORITY_GWEI":"2.0",
            "MORALIS_RATE_LIMIT_SEC":"60",
        }
    else: # balanced
        pairs={
            "RISK_PROFILE":"balanced",
            "POSITION_FRACTION":"0.002",
            "POSITION_USD_CEIL":"3",
            "MAX_SPEND_USD_PER_DAY":"6",
            "MAX_OPEN_POSITIONS":"2",
            "USD_PROFIT_MIN":"0.01",
            "GAS_MAX_FEE_GWEI":"60",
            "GAS_PRIORITY_GWEI":"1.5",
            "MORALIS_RATE_LIMIT_SEC":"90",
        }
    for k,v in pairs.items(): setattr(settings,k, v if not k.endswith("_SEC") and not k.endswith("_POSITIONS") else int(v) if v.isdigit() else v)
    _save_env(pairs)
    log(f"[PROFILE] {mode} applied")
    return {"ok":True, "profile":mode, "applied":pairs}
