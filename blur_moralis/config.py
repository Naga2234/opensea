from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

AVAILABLE_STRATEGIES = ["undercut", "mean_revert", "momentum", "hybrid"]


class Settings(BaseSettings):
    OPENSEA_API_KEY: str = "b6c32aadd9744bee9483c4637b664532"
    MORALIS_API_KEY: str = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJub25jZSI6IjBmYTRlYjRkLTMyMTItNDZkOC05"
        "NDRmLWI4M2JiZGVjZWQyYyIsIm9yZ0lkIjoiNDc3MDM4IiwidXNlcklkIjoiNDkwNzkwIiwidHlwZUlk"
        "IjoiM2U1MzQ5MTEtNzM0Yi00MjVkLWI0ZTYtMWRhM2UzMTNlMmZmIiwidHlwZSI6IlBST0pFQ1QiLCJp"
        "YXQiOjE3NjEwMzgyNDAsImV4cCI6NDkxNjc5ODI0MH0.2JLrRMZ9BdOhkt0M0KGJtcpuc4UZyToUzlw"
        "XuuHggfg"
    )
    PRIVATE_KEY: str = "0xa677990ba275736b99a3a3a37ea631be3ca7a1e1ef2db52848a1c81455e919dc"
    ADDRESS: str = "0xa57a5e84da99893364bea1fbb59dd6f437216d12"
    CHAIN: str = "polygon"   # eth | polygon
    MODE: str = "paper"  # paper | live | auto
    RISK_PROFILE: str = "balanced"
    RPC_URL: str = "https://polygon-rpc.com"
    RPC_URLS: str = (
        "[\"https://polygon-rpc.com\",\"https://rpc.ankr.com/polygon\","  # noqa: E501
        "\"https://site1.moralis-nodes.com/eth/231746d238334c139d70af0f910d5563\","  # noqa: E501
        "\"https://site2.moralis-nodes.com/eth/231746d238334c139d70af0f910d5563\"]"
    )
    POSITION_FRACTION: float = 0.005
    POSITION_USD_CEIL: float = 9.0
    MAX_SPEND_USD_PER_DAY: float = 8.0
    MAX_OPEN_POSITIONS: int = 2
    USD_PROFIT_MIN: float = 0.02
    AUTO_STOP_PROFIT_USD: float = 0.15
    STRATEGY_MODE: str = "auto"  # auto | manual
    MANUAL_STRATEGY: str = "undercut"
    GAS_MAX_FEE_GWEI: float = 80.0
    GAS_PRIORITY_GWEI: float = 2.0
    CONTRACTS: str = (
        "[\"0x67F4732266C7300cca593c814d46bee72e40659F\","
        "\"0x2b4a66557a79263275826ad31a4cddc2789334bd\","  # noqa: E501
        "\"0x86935F11C86623deC8a25696E1C19a8659CbF95d\"]"
    )
    BALANCE_SOURCE: str = "auto"  # auto | rpc | moralis
    MORALIS_RATE_LIMIT_SEC: int = 45

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

settings = Settings()

def contracts():
    import json
    try:
        return list(json.loads(settings.CONTRACTS))
    except Exception:
        return []

def rpc_urls():
    import json
    try:
        return list(json.loads(settings.RPC_URLS))
    except Exception:
        return [settings.RPC_URL] if settings.RPC_URL else []


def available_strategies():
    return list(AVAILABLE_STRATEGIES)


def normalize_strategy(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    candidate = str(name).strip()
    if not candidate:
        return None
    for item in AVAILABLE_STRATEGIES:
        if item.lower() == candidate.lower():
            return item
    return None


def strategy_state():
    mode = (getattr(settings, "STRATEGY_MODE", "auto") or "auto").lower()
    manual = normalize_strategy(getattr(settings, "MANUAL_STRATEGY", None))
    current = manual if mode == "manual" and manual else None
    return {
        "mode": "manual" if mode == "manual" else "auto",
        "manual": manual,
        "available": available_strategies(),
        "current": current,
    }
