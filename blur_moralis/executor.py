from typing import Optional

from web3 import Web3
from web3.middleware import geth_poa_middleware

from .runtime import log
from .config import settings
from .live_exec import OpenSeaExecutor, LiveNotConfigured

class Web3Helper:
    def __init__(self, rpc_url:str):
        self.rpc_url=rpc_url
        self.w3=self._make_web3(rpc_url)

    def _make_web3(self, rpc_url: Optional[str]) -> Optional[Web3]:
        if not rpc_url:
            return None
        try:
            if rpc_url.lower().startswith(("ws://", "wss://")):
                provider = Web3.WebsocketProvider(rpc_url, websocket_kwargs={"max_size": 2 ** 25})
            else:
                provider = Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20})
            w3 = Web3(provider)
            try:
                w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            except ValueError:
                pass
            return w3
        except Exception as exc:
            log(f"[RPC][ERR] failed to init provider {rpc_url}: {exc}")
            return None

    def is_ok(self)->bool:
        if not self.w3:
            return False
        try:
            if self.w3.is_connected():
                return True
        except Exception:
            pass
        try:
            self.w3.eth.block_number
            return True
        except Exception:
            return False

    def balance(self, address:str)->int:
        if not self.w3 or not address:
            return 0
        try:
            return self.w3.eth.get_balance(Web3.to_checksum_address(address))
        except Exception as exc:
            log(f"[RPC][ERR] balance fetch failed via {self.rpc_url}: {exc}")
            return 0

class PaperExecutor:
    def buy(self, trade, size_eth: float):
        tx=f"paper-{trade.get('contract')}-{trade.get('token_id')}"
        log(f"[PAPER] {tx} size={size_eth}")
        return tx

def make_executor(w3: Web3, address: str, private_key: str):
    live_mode = settings.MODE in ("live", "auto")
    if not live_mode:
        return PaperExecutor()

    missing = []
    if not settings.OPENSEA_API_KEY:
        missing.append("OPENSEA_API_KEY")
    if not private_key:
        missing.append("PRIVATE_KEY")
    addr = address or ""
    if not addr and not private_key:
        missing.append("ADDRESS")
    if missing:
        raise LiveNotConfigured(
            "Live mode requires configured " + ", ".join(missing)
        )

    try:
        return OpenSeaExecutor(w3, address, private_key)
    except LiveNotConfigured:
        raise
    except Exception as e:
        raise LiveNotConfigured(f"Failed to initialise live executor: {e}")
