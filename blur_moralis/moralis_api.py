import time, httpx
from typing import Optional, List, Dict, Any
from .config import settings
from .runtime import log

_last_call_ts = {}  # key -> ts

def _allow(key:str)->bool:
    now=time.time()
    gap = max(5, int(getattr(settings, "MORALIS_RATE_LIMIT_SEC", 60)))
    ts=_last_call_ts.get(key, 0)
    if now - ts >= gap:
        _last_call_ts[key]=now
        return True
    return False

def _chain_param()->str:
    c=(settings.CHAIN or "eth").lower()
    if c in ("eth","ethereum","mainnet"): return "eth"
    if c in ("polygon","matic"): return "polygon"
    return "eth"

def _client()->httpx.Client:
    if not settings.MORALIS_API_KEY:
        raise RuntimeError("MORALIS_API_KEY missing")
    return httpx.Client(timeout=20, headers={"X-API-Key": settings.MORALIS_API_KEY, "Accept":"application/json"})

def native_balance(address:str)->Optional[int]:
    """
    Returns balance in wei via Moralis v2: /{address}/balance
    Rate-limited by MORALIS_RATE_LIMIT_SEC to save CU.
    """
    key=f"bal:{address}:{_chain_param()}"
    if not _allow(key):
        return None  # skip to save CU
    try:
        with _client() as c:
            r=c.get(f"https://deep-index.moralis.io/api/v2/{address}/balance", params={"chain": _chain_param()})
            r.raise_for_status()
            data=r.json()
            bal=int(data.get("balance") or 0)
            log(f"[MORALIS] balance ok {address[:8]}… -> {bal}")
            return bal
    except Exception as e:
        log(f"[MORALIS][ERR] balance: {e}")
        return None

def recent_trades(contract:str, limit:int=2)->List[Dict[str,Any]]:
    """
    Optional low-CU trade glimpse for the contract (if needed).
    Using /nft/{address}/trades with small limit. Rate-limited.
    """
    key=f"trades:{contract}:{_chain_param()}"
    if not _allow(key):
        return []
    try:
        with _client() as c:
            r=c.get(f"https://deep-index.moralis.io/api/v2/nft/{contract}/trades",
                    params={"chain": _chain_param(), "marketplace": "opensea", "limit": limit})
            r.raise_for_status()
            data=r.json()
            items=data.get("result") or data.get("trades") or []
            log(f"[MORALIS] trades {contract[:8]}… -> {len(items)}")
            return items[:limit]
    except Exception as e:
        log(f"[MORALIS][ERR] trades: {e}")
        return []

def ping()->bool:
    """
    Cheap sanity: reuse balance call but without consuming too often (rate-limited).
    """
    try:
        return native_balance(settings.ADDRESS) is not None
    except Exception as e:
        log(f"[MORALIS][ERR] ping: {e}")
        return False
