import threading, time, random
from .runtime import log
from .config import settings, contracts, rpc_urls
from .executor import Web3Helper, make_executor, PaperExecutor
from .pricing import price_usd
from .moralis_api import recent_trades
from web3 import Web3

class Engine:
    _inst=None
    def __new__(cls,*a,**k):
        if not cls._inst: cls._inst=super().__new__(cls)
        return cls._inst
    def __init__(self):
        if getattr(self,'_initd',False): return
        self._initd=True; self._stop=True; self._thread=None; self._w3=None; self._ex=None
    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._stop=False; self._thread=threading.Thread(target=self.run,daemon=True); self._thread.start(); log("[ENGINE] started")
    def stop(self): self._stop=True; log("[ENGINE] stop signal")
    def _connect(self):
        urls=[settings.RPC_URL]+(rpc_urls() or [])
        for u in [x for x in urls if x]:
            try:
                h=Web3Helper(u)
                if h.is_ok(): self._w3=h.w3; log(f"[RPC] {u} chainId={self._w3.eth.chain_id} CHAIN={settings.CHAIN}"); self._ex=make_executor(self._w3, settings.ADDRESS, settings.PRIVATE_KEY); return True
            except Exception as e: log(f"[RPC] fail {u} {e}")
        return False
    def _usd_balance(self)->float:
        if not self._w3 or not settings.ADDRESS: return 0.0
        bal=self._w3.eth.get_balance(Web3.to_checksum_address(settings.ADDRESS))/1e18
        px=price_usd(settings.CHAIN) or 0.0; return bal*px
    def run(self):
        if not self._connect(): log("[ENGINE] no RPC, sleeping"); time.sleep(3); return
        log("[ENGINE] loop enter")
        from .stats import stats, risk
        px=price_usd(settings.CHAIN) or 0.0
        while not self._stop:
            for c in contracts() or []:
                if random.random()<0.1:
                    _ = recent_trades(c, limit=1)
                if random.random()<0.05:
                    strategy=random.choice(["undercut","mean_revert","momentum","hybrid"])
                    edge=abs(random.gauss(0.008,0.006))
                    fee=0.025
                    gas_usd = 0.02 if settings.CHAIN=='polygon' else 1.0
                    ev=edge - fee - (gas_usd / max(self._usd_balance(), 50.0))
                    if ev<=0 or (edge*100.0)<(settings.USD_PROFIT_MIN or 0.01):
                        log(f"[EV] skip {strategy} c={c[:8]} edge={edge:.4f} ev={ev:.4f}"); continue
                    bal_usd=self._usd_balance()
                    size_usd=min(bal_usd*float(settings.POSITION_FRACTION or 0.002), float(settings.POSITION_USD_CEIL or 3.0))
                    if bal_usd<50: size_usd=min(size_usd,5.0)
                    if bal_usd<20: size_usd=min(size_usd,3.0)
                    size_eth=size_usd/(px or 1.0)
                    trade={"contract":c,"token_id":"1","strategy":strategy,"edge":edge,"size_usd":size_usd}
                    if settings.MODE in ("live","auto") and hasattr(self._ex,"buy_token"):
                        try:
                            h=self._ex.buy_token(trade["contract"], trade["token_id"]); log(f"[LIVE][OK] {h} {trade}")
                        except Exception as e:
                            log(f"[LIVE][ERR] {e} {trade}")
                    else:
                        PaperExecutor().buy(trade, size_eth)
                        ok = random.random() < (0.52 + min(0.05, edge*10))
                        if ok:
                            stats["by_strategy"][strategy]["wins"]+=1
                            risk["pnl_today_usd"]+=max(0.01, size_usd*edge*0.5)
                        else:
                            stats["by_strategy"][strategy]["losses"]+=1
                            risk["pnl_today_usd"]-=min(0.5, size_usd*0.5)
                    time.sleep(0.2)
            time.sleep(1.0)
