import httpx, time
from http import HTTPStatus
from .runtime import log

_cache={}; _ts=0.0; _cooldown_until=0.0

def _cached_value(key:str)->float:
    value=_cache.get(key)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

def price_usd(chain:str)->float:
    global _cache,_ts,_cooldown_until
    now=time.time()
    key='eth' if chain in ('eth','ethereum') else 'polygon'
    cached=_cache.get(key)
    if cached is not None and now-_ts<30:
        return float(cached)
    if cached is not None and now<_cooldown_until:
        return float(cached)
    try:
        r=httpx.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={'ids':'ethereum,polygon','vs_currencies':'usd'},
            headers={'User-Agent':'opensea-pricing-bot/1.0'},
            timeout=10,
        )
        r.raise_for_status()
        data=r.json(); _ts=now
        _cache['eth']=float(data.get('ethereum',{}).get('usd') or 0.0)
        _cache['polygon']=float(data.get('polygon',{}).get('usd') or 0.0)
        return _cached_value(key)
    except httpx.HTTPStatusError as e:
        status=e.response.status_code
        if status==HTTPStatus.TOO_MANY_REQUESTS:
            retry_after=e.response.headers.get('Retry-After')
            try:
                wait=float(retry_after)
            except (TypeError, ValueError):
                wait=60.0
            _cooldown_until=time.time()+max(wait,30.0)
            log(f"[PRICE] coingecko rate limited (HTTP 429), reusing cached price for {key}.")
            return _cached_value(key)
        log(f"[PRICE] coingecko error: {e}")
        return _cached_value(key)
    except httpx.RequestError as e:
        log(f"[PRICE] coingecko request error: {e}")
        return _cached_value(key)
    except Exception as e:
        log(f"[PRICE] coingecko error: {e}")
        return _cached_value(key)
