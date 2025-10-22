from web3 import Web3
from .runtime import log
from .config import settings
from .live_exec import OpenSeaExecutor, LiveNotConfigured

class Web3Helper:
    def __init__(self, rpc_url:str):
        self.rpc_url=rpc_url
        self.w3=Web3(Web3.HTTPProvider(rpc_url)) if rpc_url else None
    def is_ok(self)->bool:
        try: return bool(self.w3 and self.w3.is_connected())
        except: return False
    def balance(self, address:str)->int:
        if not self.is_ok() or not address: return 0
        try: return self.w3.eth.get_balance(Web3.to_checksum_address(address))
        except: return 0

class PaperExecutor:
    def buy(self, trade, size_eth: float):
        tx=f"paper-{trade.get('contract')}-{trade.get('token_id')}"
        log(f"[PAPER] {tx} size={size_eth}")
        return tx

def make_executor(w3: Web3, address: str, private_key: str):
    if settings.MODE in ("live","auto") and settings.OPENSEA_API_KEY and private_key:
        try:
            return OpenSeaExecutor(w3, address, private_key)
        except Exception as e:
            log(f"[LIVE] disabled: {e}")
    return PaperExecutor()
