risk={"pnl_today_usd":0.0,"spend_today_usd":0.0,"loss_streak":0}
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
    return {"by_strategy":table, "best":best, "nl":nl, "_risk":{"pnl_today_usd":round(risk['pnl_today_usd'],2),"spend_today_usd":round(risk['spend_today_usd'],2),"loss_streak":risk['loss_streak']}}
