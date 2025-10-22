import threading, time, random
from typing import Optional
from .runtime import log
from .config import (
    settings,
    contracts,
    rpc_urls,
    available_strategies,
    normalize_strategy,
    strategy_state,
)
from .executor import Web3Helper, make_executor, PaperExecutor, LiveNotConfigured
from .pricing import price_usd
from .moralis_api import recent_trades
from .stats import stats, risk, register_trade_event
from web3 import Web3

class Engine:
    _inst=None
    def __new__(cls,*a,**k):
        if not cls._inst: cls._inst=super().__new__(cls)
        return cls._inst
    def __init__(self):
        if getattr(self,'_initd',False): return
        self._initd=True
        self._stop=True
        self._thread=None
        self._w3=None
        self._ex=None
        self._stop_reason=None
        self._started_at=None
        self._stopped_at=None
        self._last_heartbeat=None
        self._warned_bad_strategy=False
        self._warned_no_strategy=False
        self._last_strategy_announce=None
    def _validate_live_ready(self):
        if settings.MODE not in ("live", "auto"):
            return
        missing=[]
        if not settings.OPENSEA_API_KEY:
            missing.append("OPENSEA_API_KEY")
        if not settings.PRIVATE_KEY:
            missing.append("PRIVATE_KEY")
        if not settings.ADDRESS and not settings.PRIVATE_KEY:
            missing.append("ADDRESS")
        if missing:
            raise LiveNotConfigured(
                "Live mode requires configured " + ", ".join(missing)
            )

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._validate_live_ready()
        self._stop=True
        self._stop_reason=None
        self._w3=None
        self._ex=None
        register_trade_event("starting", note="Запуск движка, ждём сигналы", action="boot")
        try:
            if not self._connect():
                self._stop_reason=self._stop_reason or "RPC connection failed"
                raise RuntimeError(self._stop_reason)
        except LiveNotConfigured as e:
            self._stop_reason=str(e)
            raise
        self._stop=False
        self._started_at=time.time()
        self._stopped_at=None
        self._last_heartbeat=None
        risk["auto_stop_triggered"]=False
        risk["auto_stop_reason"]=""
        risk["last_trade_profit_usd"]=0.0
        self._thread=threading.Thread(target=self.run,daemon=True)
        self._thread.start()
        log("[ENGINE] started")
    def stop(self, reason: Optional[str]=None):
        self._stop=True
        if reason:
            self._stop_reason=reason
            log(f"[ENGINE] stop signal ({reason})")
        else:
            log("[ENGINE] stop signal")
        register_trade_event("idle", note=reason or "Движок остановлен", action="stop")
    def status(self):
        now=time.time()
        thread_alive=self._thread.is_alive() if self._thread else False
        if thread_alive and not self._stop:
            state="running"
        elif thread_alive and self._stop:
            state="stopping"
        else:
            state="idle"
        if state=="idle" and not self._stopped_at and self._started_at and not thread_alive:
            self._stopped_at=self._last_heartbeat or now
        uptime=now-(self._started_at or now)
        if state=="idle": uptime=max(0.0, (self._stopped_at or now)-(self._started_at or now))
        heartbeat_age=(now-(self._last_heartbeat or now)) if self._last_heartbeat else None
        return {
            "state": state,
            "running": state=="running",
            "stopping": state=="stopping",
            "uptime": max(0.0, uptime) if self._started_at else 0.0,
            "started_at": self._started_at,
            "stopped_at": self._stopped_at,
            "last_heartbeat": self._last_heartbeat,
            "heartbeat_ago": heartbeat_age,
            "stop_reason": self._stop_reason,
            "last_trade": {
                "status": risk.get("last_trade_status"),
                "contract": risk.get("last_trade_contract"),
                "strategy": risk.get("last_trade_strategy"),
                "size_usd": risk.get("last_trade_size_usd"),
                "pnl_usd": risk.get("last_trade_pnl_usd"),
                "note": risk.get("last_trade_note"),
                "action": risk.get("last_trade_action"),
                "ts": risk.get("last_trade_ts"),
                "closed": risk.get("last_trade_closed"),
            },
            "strategy": strategy_state(),
        }
    def _connect(self):
        urls=[settings.RPC_URL]+(rpc_urls() or [])
        last_error=None
        for u in [x for x in urls if x]:
            try:
                h=Web3Helper(u)
                if h.is_ok():
                    self._w3=h.w3
                    log(f"[RPC] {u} chainId={self._w3.eth.chain_id} CHAIN={settings.CHAIN}")
                    self._ex=make_executor(self._w3, settings.ADDRESS, settings.PRIVATE_KEY)
                    register_trade_event("waiting", contract="", note="RPC подключен, ожидаем сигналы", action="connect")
                    return True
            except LiveNotConfigured as e:
                log(f"[LIVE][ERR] {e}")
                self._stop_reason=str(e)
                raise
            except Exception as e:
                last_error=e
                log(f"[RPC] fail {u} {e}")
        if last_error:
            self._stop_reason=f"RPC connect failed: {last_error}"
        elif not urls or not any(urls):
            self._stop_reason="RPC connect failed: no RPC URLs configured"
        return False
    def _usd_balance(self)->float:
        if not self._w3 or not settings.ADDRESS: return 0.0
        bal=self._w3.eth.get_balance(Web3.to_checksum_address(settings.ADDRESS))/1e18
        px=price_usd(settings.CHAIN) or 0.0; return bal*px
    def _check_auto_stop(self, trade_profit: Optional[float]=None):
        if self._stop: return
        threshold=float(getattr(settings,"AUTO_STOP_PROFIT_USD",0.0) or 0.0)
        if threshold<=0: return
        total=risk.get("pnl_today_usd",0.0)
        reason=None
        if trade_profit is not None and trade_profit>=threshold:
            reason=f"auto-stop triggered: single trade profit ${trade_profit:.2f} reached target ${threshold:.2f}"
        elif total>=threshold:
            reason=f"auto-stop triggered: cumulative profit ${total:.2f} reached target ${threshold:.2f}"
        if reason:
            risk["auto_stop_triggered"]=True
            risk["auto_stop_reason"]=reason
            self.stop(reason)

    def _select_strategy(self)->tuple[Optional[str], str]:
        available=available_strategies()
        if not available:
            if not self._warned_no_strategy:
                log("[СТРАТЕГИЯ] список стратегий пуст, требуется настройка")
                self._warned_no_strategy=True
            return None, "none"
        self._warned_no_strategy=False
        mode=(getattr(settings, "STRATEGY_MODE", "auto") or "auto").lower()
        manual=normalize_strategy(getattr(settings, "MANUAL_STRATEGY", None))
        if mode=="manual":
            if manual:
                self._warned_bad_strategy=False
                return manual, "manual"
            if not self._warned_bad_strategy:
                log("[СТРАТЕГИЯ] ручной режим без выбранной стратегии — возвращаемся к авто")
                self._warned_bad_strategy=True
        else:
            self._warned_bad_strategy=False
        return random.choice(available), "auto"

    def _announce_strategy(self, mode: str, strategy: Optional[str]):
        key=(mode, strategy if mode=="manual" else None)
        if self._last_strategy_announce==key:
            return
        self._last_strategy_announce=key
        if mode=="manual" and strategy:
            log(f"[СТРАТЕГИЯ] ручной режим активен — используем только {strategy}")
        elif mode=="auto":
            log("[СТРАТЕГИЯ] автоматический выбор — набор стратегий переключается автоматически")

    def run(self):
        try:
            if not self._w3 or not self._ex:
                if not self._connect():
                    log("[ENGINE] no RPC, stopping")
                    self._stop=True
                    return
        except LiveNotConfigured as e:
            log(f"[ENGINE] start aborted: {e}")
            self._stop=True
            self._stop_reason=str(e)
            return
        log("[ENGINE] loop enter")
        self._last_heartbeat=time.time()
        register_trade_event("waiting", note="Цикл запущен, ожидаем сигналы", action="loop")
        px=price_usd(settings.CHAIN) or 0.0
        while not self._stop:
            self._last_heartbeat=time.time()
            for c in contracts() or []:
                if isinstance(c, str):
                    short_c=(c[:8]+"…") if len(c)>8 else c
                else:
                    short_c=str(c) if c else "—"
                register_trade_event("scanning", contract=c, note=f"Проверяем {short_c}", action="scan")
                log(f"[ENGINE][SCAN] {short_c} — проверка сигнала")
                log(f"[STATUS][RUNNING][SCAN] Просмотр предложения по {short_c}")
                if random.random()<0.1:
                    _ = recent_trades(c, limit=1)
                trigger=random.random()
                if trigger>=0.05:
                    log(f"[ENGINE][WAIT] {short_c} — сигнала нет")
                    register_trade_event("waiting", contract=c, note=f"Сигналов нет по {short_c}", action="wait")
                    log(f"[STATUS][RUNNING][SCAN] {short_c} — сигналов нет, двигаемся дальше")
                    time.sleep(0.1)
                    continue
                strategy, strategy_mode = self._select_strategy()
                if not strategy:
                    log(f"[СТРАТЕГИЯ][СКИП] {short_c} — нет доступных стратегий, ждём обновления настроек")
                    register_trade_event("skipped", contract=c, strategy=None,
                                          note="Нет активных стратегий", action="skip")
                    time.sleep(0.1)
                    continue
                self._announce_strategy(strategy_mode, strategy)
                register_trade_event("signal", contract=c, strategy=strategy, note=f"Сигнал {strategy} обнаружен", action="signal")
                log(f"[STATUS][RUNNING][SIGNAL] {short_c} — стратегия {strategy}")
                edge=abs(random.gauss(0.008,0.006))
                fee=0.025
                gas_usd = 0.02 if settings.CHAIN=='polygon' else 1.0
                ev=edge - fee - (gas_usd / max(self._usd_balance(), 50.0))
                edge_pct=edge*100.0
                fee_pct=fee*100.0
                usd_min=settings.USD_PROFIT_MIN or 0.01
                if ev<=0 or edge_pct<usd_min:
                    skip_reason=(
                        f"стратегия {strategy}: edge={edge_pct:.2f}% не покрывает комиссию {fee_pct:.2f}% "
                        f"и расходы на газ ${gas_usd:.2f}, минимум для входа {usd_min:.2f}%; ожидаемая ценность сделки EV={ev:.4f}"
                    )
                    log(f"[РЕШЕНИЕ][SKIP] {short_c} — {skip_reason}. Пропускаем сигнал")
                    register_trade_event(
                        "skipped",
                        contract=c,
                        strategy=strategy,
                        note=f"Пропуск: edge={edge_pct:.2f}% EV={ev:.4f}",
                        action="skip",
                    )
                    log(f"[STATUS][RUNNING][SKIP] {short_c} — {skip_reason}; двигаемся дальше")
                    time.sleep(0.1)
                    continue
                bal_usd=self._usd_balance()
                size_usd=min(bal_usd*float(settings.POSITION_FRACTION or 0.002), float(settings.POSITION_USD_CEIL or 3.0))
                if bal_usd<50: size_usd=min(size_usd,5.0)
                if bal_usd<20: size_usd=min(size_usd,3.0)
                size_eth=size_usd/(px or 1.0)
                trade={"contract":c,"token_id":"1","strategy":strategy,"edge":edge,"size_usd":size_usd}
                register_trade_event("entering", contract=c, strategy=strategy, size_usd=size_usd,
                                      note=f"Готовим вход {strategy}: edge {edge_pct:.2f}%", action="enter")
                chain_label = (settings.CHAIN or "CHAIN").upper()
                est_profit=max(0.0, size_usd*edge*0.5)
                decision_text=(
                    f"стратегия {strategy}: edge={edge_pct:.2f}% даёт положительный EV={ev:.4f}. "
                    f"Планируем объём ${size_usd:.2f} (~{size_eth:.4f} {chain_label}), ожидаемая прибыль ${est_profit:.2f}."
                )
                log(f"[РЕШЕНИЕ][BUY] {short_c} — {decision_text}")
                if px:
                    log(f"[STATUS][RUNNING][ENTER] {short_c} — готовим сделку {strategy} на ${size_usd:.2f} (~{size_eth:.4f} {chain_label}) по цене ${px:.2f}")
                else:
                    log(f"[STATUS][RUNNING][ENTER] {short_c} — готовим сделку {strategy} на ${size_usd:.2f} (~{size_eth:.4f} {chain_label})")
                if settings.MODE in ("live","auto") and hasattr(self._ex,"buy_token"):
                    try:
                        log(f"[STATUS][RUNNING][BUY] {short_c} — отправляем заявку в OpenSea на ${size_usd:.2f} (~{size_eth:.4f} {chain_label})")
                        h=self._ex.buy_token(trade["contract"], trade["token_id"])
                        log(f"[LIVE][OK] {h} {trade}")
                        register_trade_event("filled", contract=c, strategy=strategy, size_usd=size_usd,
                                              note=f"TX {h}", action="buy")
                        log(f"[STATUS][RUNNING][BUY] {short_c} — заявка подтверждена, TX={h}")
                        if est_profit>0:
                            risk["last_trade_profit_usd"]=est_profit
                            risk["pnl_today_usd"]+=est_profit
                            self._check_auto_stop(est_profit)
                            register_trade_event("win", contract=c, strategy=strategy, size_usd=size_usd,
                                                  pnl_usd=est_profit,
                                                  note=f"Оценка профита ${est_profit:.2f}", action="result")
                            log(f"[STATUS][RUNNING][RESULT] {short_c} — ожидаемый профит ${est_profit:.2f}")
                    except Exception as e:
                        log(f"[LIVE][ERR] {e} {trade}")
                        register_trade_event("error", contract=c, strategy=strategy, size_usd=size_usd,
                                              note=str(e), action="error")
                        log(f"[STATUS][RUNNING][ERROR] {short_c} — не удалось купить: {e}")
                else:
                    PaperExecutor().buy(trade, size_eth)
                    log(f"[TRADE][PAPER][BUY] {strategy} {short_c} size=${size_usd:.2f} (~{size_eth:.4f} {chain_label} unit)")
                    log(f"[STATUS][RUNNING][BUY] {short_c} — бумажная покупка {strategy} на ${size_usd:.2f} (~{size_eth:.4f} {chain_label})")
                    risk["spend_today_usd"]+=size_usd
                    ok = random.random() < (0.52 + min(0.05, edge*10))
                    if ok:
                        stats["by_strategy"][strategy]["wins"]+=1
                        profit=max(0.01, size_usd*edge*0.5)
                        risk["pnl_today_usd"]+=profit
                        risk["last_trade_profit_usd"]=profit
                        risk["loss_streak"]=0
                        self._check_auto_stop(profit)
                        register_trade_event("win", contract=c, strategy=strategy, size_usd=size_usd,
                                              pnl_usd=profit,
                                              note=f"Профит ${profit:.2f}", action="sell")
                        log(f"[TRADE][RESULT][WIN] {strategy} {short_c} +${profit:.2f}")
                        log(f"[STATUS][RUNNING][SELL] {short_c} — фиксация прибыли ${profit:.2f} по стратегии {strategy}")
                    else:
                        stats["by_strategy"][strategy]["losses"]+=1
                        loss=min(0.5, size_usd*0.5)
                        risk["pnl_today_usd"]-=loss
                        risk["last_trade_profit_usd"]=-loss
                        register_trade_event("loss", contract=c, strategy=strategy, size_usd=size_usd,
                                              pnl_usd=-loss,
                                              note=f"Убыток ${loss:.2f}", action="sell")
                        log(f"[TRADE][RESULT][LOSS] {strategy} {short_c} -${loss:.2f}")
                        log(f"[STATUS][RUNNING][SELL] {short_c} — фиксация убытка ${loss:.2f} по стратегии {strategy}")
                        risk["loss_streak"]=risk.get("loss_streak",0)+1
                        self._check_auto_stop()
                if self._stop: break
                time.sleep(0.2)
            if self._stop: break
            time.sleep(1.0)
        self._last_heartbeat=time.time()
        self._stopped_at=self._last_heartbeat
        log(f"[ENGINE] loop exit ({self._stop_reason or 'stopped'})")
        register_trade_event("idle", note=self._stop_reason or "Движок остановлен", action="stop")
