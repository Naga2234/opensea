from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    OPENSEA_API_KEY: str = ""
    MORALIS_API_KEY: str = ""
    PRIVATE_KEY: str = ""
    ADDRESS: str = ""
    CHAIN: str = "eth"   # eth | polygon
    MODE: str = "paper"  # paper | live | auto
    RISK_PROFILE: str = "conservative"
    RPC_URL: str = ""
    RPC_URLS: str = "[]"
    POSITION_FRACTION: float = 0.002
    POSITION_USD_CEIL: float = 3.0
    MAX_SPEND_USD_PER_DAY: float = 6.0
    MAX_OPEN_POSITIONS: int = 1
    USD_PROFIT_MIN: float = 0.01
    GAS_MAX_FEE_GWEI: float = 60.0
    GAS_PRIORITY_GWEI: float = 1.5
    CONTRACTS: str = "[]"
    BALANCE_SOURCE: str = "auto"  # auto | rpc | moralis
    MORALIS_RATE_LIMIT_SEC: int = 60

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
