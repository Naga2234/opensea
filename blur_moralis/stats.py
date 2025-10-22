import time
from typing import Optional

risk={
    "pnl_today_usd":0.0,
    "spend_today_usd":0.0,
    "loss_streak":0,
    "last_trade_profit_usd":0.0,
    "auto_stop_triggered":False,
    "auto_stop_reason":"",
    "last_trade_status":"idle",
    "last_trade_contract":"",
    "last_trade_strategy":"",
    "last_trade_size_usd":0.0,
    "last_trade_size_native":0.0,
    "last_trade_pnl_usd":0.0,
    "last_trade_pnl_native":0.0,
    "last_trade_note":"",
    "last_trade_action":"",
    "last_trade_ts":0.0,
    "last_trade_closed":True,
    "last_trade_symbol":"",
}
stats={"by_strategy":{
    "undercut":{"wins":0,"losses":0,"avg_edge":0.0},
    "mean_revert":{"wins":0,"losses":0,"avg_edge":0.0},
    "momentum":{"wins":0,"losses":0,"avg_edge":0.0},
    "hybrid":{"wins":0,"losses":0,"avg_edge":0.0},
}}

def kpi():
    out={}
    for k,v in stats["by_strategy"].items():
        n=v["wins"]+v["losses"]
        wr= (100.0*v["wins"]/n) if n>0 else 0.0
        out[k]={"winrate": round(wr,2)}
    out["_risk"]=risk
    return out


def register_trade_event(
    status: str,
    *,
    contract: Optional[str] = None,
    strategy: Optional[str] = None,
    size_usd: Optional[float] = None,
    size_native: Optional[float] = None,
    pnl_usd: Optional[float] = None,
    pnl_native: Optional[float] = None,
    note: Optional[str] = None,
    action: Optional[str] = None,
    symbol: Optional[str] = None,
):
    now = time.time()
    risk["last_trade_status"] = status
    risk["last_trade_ts"] = now
    if contract is not None:
        risk["last_trade_contract"] = contract
    if strategy is not None:
        risk["last_trade_strategy"] = strategy
    if size_usd is not None:
        risk["last_trade_size_usd"] = round(float(size_usd), 4)
    if size_native is not None:
        risk["last_trade_size_native"] = round(float(size_native), 6)
    if pnl_usd is not None:
        risk["last_trade_pnl_usd"] = round(float(pnl_usd), 4)
    if pnl_native is not None:
        risk["last_trade_pnl_native"] = round(float(pnl_native), 6)
    if note is not None:
        risk["last_trade_note"] = note
    if action is not None:
        risk["last_trade_action"] = action
    if symbol is not None:
        risk["last_trade_symbol"] = symbol
    risk["last_trade_closed"] = status in {"idle", "waiting", "skipped", "win", "loss", "filled", "error"}

def _score(v:dict)->float:
    n=v["wins"]+v["losses"]
    wr=(v["wins"]/n) if n>0 else 0.0
    edge=max(0.0, v.get("avg_edge",0.0)) # fraction
    base=0.6*wr + 0.3*edge
    bonus=0.1*(min(n,50)/50.0)
    return base+bonus

def leaderboard(min_trades:int=3):
    table={}
    for name,v in stats["by_strategy"].items():
        n=v["wins"]+v["losses"]
        enough = n>=min_trades
        table[name]={ "enough":enough, "n":n,
           "winrate":(v["wins"]/n*100.0) if n>0 else 0.0,
           "avg_edge_pct":v.get("avg_edge",0.0)*100.0,
           "score":(_score(v)*100.0 if enough else 0.0) }
    enough={k:v for k,v in table.items() if v["enough"]}
    best=max(enough.items(), key=lambda kv: kv[1]["score"])[0] if enough else None
    if best:
        b=table[best]; nl=f"Сейчас лидирует «{best}»: винрейт {b['winrate']:.1f}% при ср. edge {b['avg_edge_pct']:.2f}% на выборке {b['n']} сделок."
    else:
        most=max(table.items(), key=lambda kv: kv[1]["n"])[0] if table else None
        nl=(f"Пока мало данных. Больше всего данных по «{most}»: {table[most]['n']} сделок, винрейт {table[most]['winrate']:.1f}%." if most else "Статистика появится после первых сделок.")
    return {
        "by_strategy":table,
        "best":best,
        "nl":nl,
        "_risk":{
            "pnl_today_usd":round(risk['pnl_today_usd'],2),
            "spend_today_usd":round(risk['spend_today_usd'],2),
            "loss_streak":risk['loss_streak'],
            "last_trade_profit_usd":round(risk.get('last_trade_profit_usd',0.0),2),
            "auto_stop_triggered":bool(risk.get('auto_stop_triggered')),
            "auto_stop_reason":risk.get('auto_stop_reason',''),
            "last_trade_status":risk.get('last_trade_status','idle'),
            "last_trade_contract":risk.get('last_trade_contract',''),
            "last_trade_strategy":risk.get('last_trade_strategy',''),
            "last_trade_size_usd":risk.get('last_trade_size_usd',0.0),
            "last_trade_size_native":risk.get('last_trade_size_native',0.0),
            "last_trade_pnl_usd":risk.get('last_trade_pnl_usd',0.0),
            "last_trade_pnl_native":risk.get('last_trade_pnl_native',0.0),
            "last_trade_note":risk.get('last_trade_note',''),
            "last_trade_action":risk.get('last_trade_action',''),
            "last_trade_ts":risk.get('last_trade_ts',0.0),
            "last_trade_closed":bool(risk.get('last_trade_closed',True)),
            "last_trade_symbol":risk.get('last_trade_symbol',''),
        },
    }
