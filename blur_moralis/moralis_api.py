import time, httpx
from typing import Optional, List, Dict, Any
from .config import settings
from .runtime import log

_last_call_ts = {}  # key -> ts
_usage_cache: Dict[str, Any] = {"data": None, "fingerprint": None}
_last_usage_log_ts: float = 0.0
_last_usage_error_ts: float = 0.0
_balance_cache: Dict[str, int] = {}

def _allow(key: str, *, gap: Optional[int] = None) -> bool:
    now=time.time()
    gap = gap if gap is not None else max(5, int(getattr(settings, "MORALIS_RATE_LIMIT_SEC", 60)))
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
    cached=_balance_cache.get(key)
    if not address:
        return cached
    if not _allow(key):
        return cached  # skip to save CU
    try:
        with _client() as c:
            r=c.get(f"https://deep-index.moralis.io/api/v2/{address}/balance", params={"chain": _chain_param()})
            r.raise_for_status()
            data=r.json()
            bal=int(data.get("balance") or 0)
            log(f"[MORALIS] balance ok {address[:8]}… -> {bal}")
            _balance_cache[key]=bal
            return bal
    except Exception as e:
        log(f"[MORALIS][ERR] balance: {e}")
        return cached

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


def _extract_numeric(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _normalize_usage_payload(data: Any) -> Dict[str, Any]:
    """Best-effort extraction of Moralis usage payload."""

    fields: Dict[str, Any] = {}

    def _walk(node: Any):
        if isinstance(node, dict):
            local: Dict[str, Any] = {}
            for key, value in node.items():
                key_lower = key.lower()
                if isinstance(value, (dict, list)):
                    _walk(value)
                    continue
                num = _extract_numeric(value)
                if num is None:
                    if isinstance(value, str) and any(t in key_lower for t in ("period", "reset", "window")):
                        fields.setdefault("period", value)
                    continue
                if "current" in key_lower and ("cu" in key_lower or "usage" in key_lower or "used" in key_lower):
                    local.setdefault("current", num)
                elif ("limit" in key_lower or "quota" in key_lower) and ("cu" in key_lower or "credit" in key_lower):
                    local.setdefault("limit", num)
                elif any(tag in key_lower for tag in ("remaining", "left", "available")):
                    local.setdefault("remaining", num)
                elif "reset" in key_lower:
                    fields.setdefault("reset_at", num)
            if local:
                fields.update({k: local[k] for k in ("current", "limit", "remaining") if k in local})
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(data)
    if "limit" in fields and "current" in fields and "remaining" not in fields:
        fields["remaining"] = max(0.0, fields["limit"] - fields["current"])
    return fields


def _usage_fingerprint(payload: Dict[str, Any]) -> str:
    def _rounded(value: Any) -> str:
        num = _extract_numeric(value)
        if num is None:
            return str(value)
        return str(round(num, 4))

    keys = [
        _rounded(payload.get("current", -1)),
        _rounded(payload.get("limit", -1)),
        _rounded(payload.get("remaining", -1)),
        str(payload.get("period", "")),
        str(payload.get("reset_at", "")),
    ]
    return "|".join(keys)


def _format_usage_summary(payload: Optional[Dict[str, Any]]) -> str:
    if not payload:
        return "нет данных"
    current_raw = payload.get("current")
    limit_raw = payload.get("limit")
    remaining_raw = payload.get("remaining")
    current = _extract_numeric(current_raw)
    limit = _extract_numeric(limit_raw)
    remaining = _extract_numeric(remaining_raw)
    parts = []
    if current is not None and limit is not None:
        parts.append(f"{current:.2f}/{limit:.2f} CU")
    elif current is not None:
        parts.append(f"{current:.2f} CU")
    elif current_raw is not None:
        parts.append(str(current_raw))
    if remaining is not None:
        parts.append(f"осталось {remaining:.2f} CU")
    elif remaining_raw is not None:
        parts.append(f"осталось {remaining_raw}")
    period = payload.get("period")
    if period:
        parts.append(str(period))
    reset_at = payload.get("reset_at")
    if reset_at:
        parts.append(f"reset {reset_at}")
    return " · ".join(parts) if parts else "payload"


def current_cu_usage(force: bool = False) -> Optional[Dict[str, Any]]:
    """Return Moralis Current CU Usage with basic caching and logging."""

    global _last_usage_log_ts, _last_usage_error_ts

    if not settings.MORALIS_API_KEY:
        log("[MORALIS][ERR] usage: MORALIS_API_KEY missing")
        return None

    cache = _usage_cache.get("data")
    allow = force or _allow("usage", gap=15)
    now = time.time()
    if not allow and cache:
        # Log cached value periodically
        if now - _last_usage_log_ts >= 30.0:
            summary = _format_usage_summary(cache)
            log(f"[MORALIS][USAGE] cached usage -> {summary}")
            _last_usage_log_ts = now
        return cache

    endpoints = (
        ("https://deep-index.moralis.io/api/v2.2/info/usage", {"type": "evm"}),
        ("https://deep-index.moralis.io/api/v2/info/usage", {"type": "evm"}),
        ("https://deep-index.moralis.io/api/v2.2/info/usage", None),
        ("https://deep-index.moralis.io/api/v2/info/usage", None),
    )
    last_error: Optional[Exception] = None
    last_status: Optional[int] = None
    try:
        with _client() as c:
            for url, params in endpoints:
                try:
                    r = c.get(url, params=params or None)
                    if r.status_code == 204:
                        continue
                    r.raise_for_status()
                    raw = r.json()
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    last_status = exc.response.status_code if exc.response is not None else None
                    if last_status == 404:
                        continue
                    continue
                except Exception as exc:
                    last_error = exc
                    last_status = None
                    continue
                else:
                    fields = _normalize_usage_payload(raw)
                    payload = {"fetched_at": now, **fields, "raw": raw, "endpoint": url, "params": params or {}}
                    fingerprint = _usage_fingerprint(payload)
                    _usage_cache.update({"data": payload, "fingerprint": fingerprint})
                    summary = _format_usage_summary(payload)
                    log(f"[MORALIS][USAGE] Current CU Usage: {summary}")
                    _last_usage_log_ts = now
                    _last_usage_error_ts = now
                    return payload
    except Exception as exc:
        last_error = exc
        last_status = None

    if last_status == 404:
        if now - _last_usage_error_ts >= 60.0:
            log("[MORALIS][WARN] usage endpoint returned 404 — возможно, функция не доступна для вашего ключа")
            _last_usage_error_ts = now
        return cache

    if last_error and now - _last_usage_error_ts >= 30.0:
        log(f"[MORALIS][ERR] usage fetch failed: {last_error}")
        _last_usage_error_ts = now
    return cache
