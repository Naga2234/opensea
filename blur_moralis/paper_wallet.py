"""Paper trading balance and collection tracking."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .runtime import log

_State = Dict[str, Any]

_state: _State = {
    "initialized": False,
    "initial_native": 0.0,
    "balance_native": 0.0,
    "last_price": None,
    "symbol": "",
    "positions": [],
    "history": [],
}


def _to_float(value: Optional[float], default: float = 0.0) -> float:
    try:
        if value is None:
            raise TypeError
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _resolve_price(price: Optional[float]) -> float:
    if price is not None:
        px = _to_float(price, 0.0)
        if px > 0:
            _state["last_price"] = px
    return _to_float(_state.get("last_price"), 0.0)


def _normalize_native(
    native: Optional[float],
    usd: Optional[float],
    price: Optional[float],
) -> float:
    if native is not None:
        try:
            return float(native)
        except (TypeError, ValueError):
            pass
    px = _to_float(price, 0.0)
    if px > 0 and usd is not None:
        try:
            return float(usd) / px
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def reset() -> None:
    """Clear state (primarily for tests)."""
    _state.update(
        {
            "initialized": False,
            "initial_native": 0.0,
            "balance_native": 0.0,
            "last_price": None,
            "symbol": "",
            "positions": [],
            "history": [],
        }
    )


def bootstrap(
    balance_native: Optional[float],
    *,
    price: Optional[float] = None,
    symbol: Optional[str] = None,
) -> None:
    px = _resolve_price(price)
    if symbol:
        _state["symbol"] = symbol
    if not _state["initialized"]:
        native = _normalize_native(balance_native, None, px) if balance_native is not None else 0.0
        _state["initialized"] = True
        _state["initial_native"] = native
        _state["balance_native"] = native
        log(f"[PAPER][BOOT] balance_native={native:.6f} symbol={_state['symbol'] or symbol or ''}")


def record_buy(
    trade: Dict[str, Any],
    *,
    size_native: Optional[float],
    size_usd: Optional[float],
    price: Optional[float],
    symbol: Optional[str] = None,
) -> None:
    px = _resolve_price(price)
    if symbol:
        _state["symbol"] = symbol
    native_size = _normalize_native(size_native, size_usd, px)
    if native_size <= 0:
        return
    if not _state["initialized"]:
        bootstrap(native_size, price=px, symbol=symbol)
    _state["balance_native"] -= native_size
    position = {
        "contract": trade.get("contract", ""),
        "token_id": str(trade.get("token_id", "")),
        "strategy": trade.get("strategy", ""),
        "size_native": native_size,
        "entry_price": px,
        "entry_usd": native_size * px if px else _to_float(size_usd, 0.0),
        "entered_at": time.time(),
    }
    _state.setdefault("positions", []).append(position)
    _state.setdefault("history", []).append(
        {
            "ts": position["entered_at"],
            "type": "buy",
            "contract": position["contract"],
            "token_id": position["token_id"],
            "size_native": native_size,
            "price": px,
        }
    )
    log(
        f"[PAPER][COLLECT] +{native_size:.6f} native (positions={len(_state['positions'])})"
    )


def record_result(
    contract: Optional[str],
    token_id: Optional[str],
    *,
    pnl_native: Optional[float],
    pnl_usd: Optional[float],
    price: Optional[float],
    symbol: Optional[str] = None,
) -> None:
    px = _resolve_price(price)
    if symbol:
        _state["symbol"] = symbol
    contract_key = contract or ""
    token_key = str(token_id or "")
    positions: List[Dict[str, Any]] = _state.setdefault("positions", [])
    idx = next(
        (i for i, pos in enumerate(positions) if pos.get("contract") == contract_key and pos.get("token_id") == token_key),
        None,
    )
    base_native = 0.0
    if idx is not None:
        pos = positions.pop(idx)
        base_native = _to_float(pos.get("size_native"), 0.0)
        _state.setdefault("history", []).append(
            {
                "ts": time.time(),
                "type": "sell",
                "contract": contract_key,
                "token_id": token_key,
                "size_native": base_native,
                "price": px,
            }
        )
    pnl_native_value = _normalize_native(pnl_native, pnl_usd, px)
    _state["balance_native"] += base_native + pnl_native_value
    log(
        f"[PAPER][RESULT] Δnative={pnl_native_value:+.6f} balance={_state['balance_native']:.6f}"
    )


def snapshot(
    *,
    price: Optional[float] = None,
    symbol: Optional[str] = None,
) -> Dict[str, Any]:
    px = _resolve_price(price)
    if symbol:
        _state["symbol"] = symbol
    balance_native = _to_float(_state.get("balance_native"), 0.0)
    initial_native = _to_float(_state.get("initial_native"), 0.0)
    balance_usd = balance_native * px if px else None
    initial_usd = initial_native * px if px else None
    positions_view: List[Dict[str, Any]] = []
    for item in _state.get("positions", []):
        size_native = _to_float(item.get("size_native"), 0.0)
        entry_price = _to_float(item.get("entry_price"), px)
        size_usd = size_native * entry_price if entry_price else None
        positions_view.append(
            {
                "contract": item.get("contract", ""),
                "token_id": item.get("token_id", ""),
                "strategy": item.get("strategy", ""),
                "size_native": size_native,
                "size_usd": size_usd,
                "entered_at": item.get("entered_at"),
            }
        )
    pnl_native = balance_native - initial_native
    pnl_usd = None
    if balance_usd is not None and initial_usd is not None:
        pnl_usd = balance_usd - initial_usd
    return {
        "initialized": bool(_state.get("initialized")),
        "balance_native": balance_native,
        "balance_usd": balance_usd,
        "initial_native": initial_native,
        "initial_usd": initial_usd,
        "pnl_native": pnl_native,
        "pnl_usd": pnl_usd,
        "symbol": _state.get("symbol", ""),
        "last_price": px,
        "positions": positions_view,
        "history": list(_state.get("history", []))[-200:],
    }
