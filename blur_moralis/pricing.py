import httpx, time
from .runtime import log
_cache={}; _ts=0
def price_usd(chain:str)->float:
    global _cache,_ts
    now=time.time(); key='eth' if chain in ('eth','ethereum') else 'polygon'
    if _cache.get(key) and now-_ts<30: return _cache[key]
    try:
        r=httpx.get('https://api.coingecko.com/api/v3/simple/price',
                    params={'ids':'ethereum,polygon','vs_currencies':'usd'}, timeout=10)
        r.raise_for_status(); data=r.json(); _ts=now
        _cache['eth']=float(data.get('ethereum',{}).get('usd') or 0)
        _cache['polygon']=float(data.get('polygon',{}).get('usd') or 0)
        return _cache[key]
    except Exception as e:
        log(f"[PRICE] coingecko error: {e}"); return 0.0
