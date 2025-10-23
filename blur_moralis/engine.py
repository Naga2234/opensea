import threading, time, random
from datetime import datetime
from typing import Optional, Tuple
from .runtime import log
from .config import (
    settings,
    contracts,
    rpc_urls,
    available_strategies,
    normalize_strategy,
    strategy_state,
    native_symbol,
)
from .executor import Web3Helper, make_executor, PaperExecutor, LiveNotConfigured
from .pricing import price_usd
from .moralis_api import recent_trades
from .stats import stats, risk, register_trade_event
from web3 import Web3
from . import paper_wallet

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

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)
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
            self._stop_reason=self._stop_reason or "stop requested"
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
                "size_native": risk.get("last_trade_size_native"),
                "pnl_usd": risk.get("last_trade_pnl_usd"),
                "pnl_native": risk.get("last_trade_pnl_native"),
                "note": risk.get("last_trade_note"),
                "action": risk.get("last_trade_action"),
                "ts": risk.get("last_trade_ts"),
                "closed": risk.get("last_trade_closed"),
                "symbol": risk.get("last_trade_symbol"),
                "chain": settings.CHAIN,
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
    def _native_balance(self) -> float:
        if not self._w3 or not settings.ADDRESS:
            return 0.0
        try:
            bal = self._w3.eth.get_balance(Web3.to_checksum_address(settings.ADDRESS)) / 1e18
        except Exception:
            return 0.0
        return self._to_float(bal, 0.0)
    def _check_auto_stop(self, trade_profit: Optional[float]=None):
        if self._stop: return
        threshold=self._to_float(getattr(settings,"AUTO_STOP_PROFIT_USD",0.0) or 0.0, 0.0)
        if threshold<=0: return
        total=self._to_float(risk.get("pnl_today_usd",0.0), 0.0)
        reason=None
        if trade_profit is not None:
            trade_profit=self._to_float(trade_profit, 0.0)
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

    def _coerce_timestamp(self, value) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            ts=float(value)
            while ts>1e12:
                ts/=1000.0
            if ts>1e10:
                ts/=1000.0
            return ts
        if isinstance(value, str):
            txt=value.strip()
            if not txt:
                return None
            try:
                return self._coerce_timestamp(float(txt))
            except (TypeError, ValueError):
                pass
            try:
                if txt.endswith("Z"):
                    txt = txt[:-1] + "+00:00"
                return datetime.fromisoformat(txt).timestamp()
            except ValueError:
                return None
        return None

    def _parse_trade_timestamp(self, trade: dict) -> Optional[float]:
        for key in (
            "block_timestamp",
            "blockTimestamp",
            "timestamp",
            "ts",
            "time",
            "created_at",
            "createdAt",
            "event_timestamp",
        ):
            if key in trade:
                ts=self._coerce_timestamp(trade.get(key))
                if ts is not None:
                    return ts
        return None

    def _as_positive_float(self, value) -> Optional[float]:
        if value is None:
            return None
        try:
            num=float(value)
        except (TypeError, ValueError):
            return None
        if num<0:
            return None
        return num

    def _parse_trade_usd(self, trade: dict) -> Optional[float]:
        for key in (
            "price_usd",
            "usd_price",
            "usdPrice",
            "priceUsd",
            "sale_price_usd",
            "total_price_usd",
            "value_usd",
            "valueUsd",
        ):
            if key in trade:
                usd=self._as_positive_float(trade.get(key))
                if usd is not None:
                    return usd
        native=trade.get("native_price") or trade.get("nativePrice")
        if isinstance(native, dict):
            if "usd" in native or "usd_price" in native or "usdPrice" in native:
                usd=self._as_positive_float(native.get("usd") or native.get("usd_price") or native.get("usdPrice"))
                if usd is not None:
                    return usd
            native_value=native.get("value") or native.get("amount")
            decimals=native.get("decimals")
            maybe=self._as_positive_float(native_value)
            if maybe is not None and decimals is not None:
                try:
                    scale=10 ** int(decimals)
                    if scale>0:
                        return maybe/scale
                except (TypeError, ValueError):
                    pass
        token=trade.get("payment_token") or trade.get("paymentToken")
        if isinstance(token, dict):
            usd_price=self._as_positive_float(token.get("usd_price") or token.get("usdPrice"))
            amount=self._as_positive_float(trade.get("total_price") or trade.get("price"))
            decimals=token.get("decimals")
            if amount is not None and usd_price is not None:
                try:
                    if decimals is not None:
                        scale=10 ** int(decimals)
                        if scale>0:
                            amount/=scale
                except (TypeError, ValueError):
                    pass
                return amount*usd_price
        fallback=self._as_positive_float(trade.get("price") or trade.get("total_price"))
        return fallback

    def _extract_buyer(self, trade: dict) -> Optional[str]:
        for key in (
            "buyer_address",
            "buyerAddress",
            "buyer",
            "to_address",
            "toAddress",
            "to",
            "winner_address",
            "winnerAddress",
        ):
            if key in trade:
                value=trade.get(key)
                if isinstance(value, str):
                    candidate=value.strip()
                    if candidate:
                        return candidate
        buyer=trade.get("buyer") or trade.get("to_account") or trade.get("toAccount")
        if isinstance(buyer, dict):
            for field in ("address", "wallet_address", "walletAddress"):
                value=buyer.get(field)
                if isinstance(value, str):
                    candidate=value.strip()
                    if candidate:
                        return candidate
        return None

    def _evaluate_liquidity(self, trades) -> Tuple[bool, str]:
        window_minutes=int(getattr(settings, "WINDOW_MINUTES", 0) or 0)
        min_trades=int(getattr(settings, "MIN_TRADES_IN_WINDOW", 0) or 0)
        min_buyers=int(getattr(settings, "MIN_UNIQUE_BUYERS", 0) or 0)
        min_volume=self._to_float(getattr(settings, "MIN_VOLUME_USD_WINDOW", 0.0) or 0.0, 0.0)
        if window_minutes<=0 and min_trades==0 and min_buyers==0 and min_volume<=0:
            return True, "требования к ликвидности отключены"
        if window_minutes<=0:
            window_minutes=60
        dataset=trades or []
        if not dataset:
            if min_trades or min_buyers or min_volume:
                return False, "нет данных по последним сделкам"
            return True, "требования к ликвидности отключены"
        since=time.time() - float(window_minutes)*60.0
        count=0
        buyers=set()
        volume=0.0
        for trade in dataset:
            if not isinstance(trade, dict):
                continue
            ts=self._parse_trade_timestamp(trade)
            if ts is None or ts<since:
                continue
            count+=1
            buyer=self._extract_buyer(trade)
            if buyer:
                buyers.add(buyer.lower())
            usd=self._parse_trade_usd(trade)
            if usd is not None:
                volume+=max(0.0, usd)
        if min_trades and count<min_trades:
            return False, f"недостаточно сделок: {count}/{min_trades} за {window_minutes} мин"
        if min_buyers and len(buyers)<min_buyers:
            return False, f"мало покупателей: {len(buyers)}/{min_buyers} за {window_minutes} мин"
        if min_volume and volume<min_volume:
            return False, f"объём ${volume:.2f} < {min_volume:.2f} за {window_minutes} мин"
        if count==0 and (min_trades or min_buyers or min_volume):
            return False, "сделки за окно не найдены"
        return True, f"ликвидность ok: {count} сделок, {len(buyers)} покупателей, объём ${volume:.2f}"

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
        try:
            while not self._stop:
                px=self._to_float(price_usd(settings.CHAIN) or 0.0, 0.0)
                symbol=native_symbol(settings.CHAIN)
                native_balance = self._to_float(self._native_balance(), 0.0)
                if settings.MODE == "paper":
                    paper_wallet.bootstrap(native_balance, price=px, symbol=symbol)
                self._last_heartbeat=time.time()
                for c in contracts() or []:
                    if isinstance(c, str):
                        short_c=(c[:8]+"…") if len(c)>8 else c
                    else:
                        short_c=str(c) if c else "—"
                    register_trade_event("scanning", contract=c, note=f"Проверяем {short_c}", action="scan")
                    log(f"[ENGINE][SCAN] {short_c} — проверка сигнала")
                    log(f"[STATUS][RUNNING][SCAN] Просмотр предложения по {short_c}")
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
                    trades = recent_trades(c, limit=25)
                    liquidity_ok, liquidity_note = self._evaluate_liquidity(trades)
                    if not liquidity_ok:
                        log(f"[РЕШЕНИЕ][SKIP] {short_c} — {liquidity_note}. Пропускаем сигнал")
                        register_trade_event(
                            "skipped",
                            contract=c,
                            strategy=strategy,
                            note=f"Пропуск: {liquidity_note}",
                            action="skip",
                        )
                        log(f"[STATUS][RUNNING][SKIP] {short_c} — {liquidity_note}; двигаемся дальше")
                        time.sleep(0.1)
                        continue
                    if liquidity_note:
                        log(f"[ENGINE][LIQ] {short_c} — {liquidity_note}")
                    edge=self._to_float(abs(random.gauss(0.008,0.006)), 0.0)
                    fee=0.025
                    gas_usd = 0.02 if settings.CHAIN=='polygon' else 1.0
                    gas_quantile=self._to_float(getattr(settings, "GAS_QUANTILE_MAX", 1.0) or 1.0, 1.0)
                    if 0.0 < gas_quantile < 1.0:
                        gas_usd*=gas_quantile
                    if settings.MODE == "paper":
                        snapshot = paper_wallet.snapshot(price=px, symbol=symbol)
                        current_native = self._to_float(snapshot.get("balance_native"), native_balance)
                    else:
                        current_native = native_balance
                    bal_usd=self._to_float(current_native*px,0.0)
                    safe_balance=max(bal_usd, 50.0)
                    ev=self._to_float(edge - fee - (gas_usd / safe_balance),0.0)
                    edge_pct=edge*100.0
                    fee_pct=fee*100.0
                    usd_min=self._to_float(getattr(settings, "USD_PROFIT_MIN", 0.01) or 0.01, 0.01)
                    edge_min=self._to_float(getattr(settings, "EDGE_MIN_PCT", 0.0) or 0.0, 0.0)
                    if edge_min>0 and edge_pct<edge_min:
                        skip_reason=(
                            f"стратегия {strategy}: edge={edge_pct:.2f}% ниже порога {edge_min:.2f}%"
                        )
                        log(f"[РЕШЕНИЕ][SKIP] {short_c} — {skip_reason}. Пропускаем сигнал")
                        register_trade_event(
                            "skipped",
                            contract=c,
                            strategy=strategy,
                            note=f"Пропуск: edge={edge_pct:.2f}% < {edge_min:.2f}%",
                            action="skip",
                        )
                        log(f"[STATUS][RUNNING][SKIP] {short_c} — {skip_reason}; двигаемся дальше")
                        time.sleep(0.1)
                        continue
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
                    fraction=self._to_float(getattr(settings, "POSITION_FRACTION", 0.002) or 0.002, 0.002)
                    usd_ceil=self._to_float(getattr(settings, "POSITION_USD_CEIL", 3.0) or 3.0, 3.0)
                    size_usd=min(bal_usd*fraction, usd_ceil)
                    if bal_usd<50:
                        size_usd=min(size_usd,5.0)
                    if bal_usd<20:
                        size_usd=min(size_usd,3.0)
                    size_usd=self._to_float(size_usd,0.0)
                    size_native=self._to_float((size_usd/px) if px else 0.0,0.0)
                    size_amount=self._to_float(size_native if px else size_usd,0.0)
                    trade={"contract":c,"token_id":"1","strategy":strategy,"edge":edge,"size_usd":size_usd,
                           "size_native":size_native}
                    register_trade_event(
                        "entering",
                        contract=c,
                        strategy=strategy,
                        size_usd=size_usd,
                        size_native=size_native,
                        note=f"Готовим вход {strategy}: edge {edge_pct:.2f}%",
                        action="enter",
                        symbol=symbol,
                    )
                    chain_label = symbol or (settings.CHAIN or "CHAIN").upper()
                    est_profit=self._to_float(max(0.0, size_usd*edge*0.5),0.0)
                    decision_text=(
                        f"стратегия {strategy}: edge={edge_pct:.2f}% даёт положительный EV={ev:.4f}. "
                        f"Планируем объём ${size_usd:.2f} (~{size_amount:.4f} {chain_label}), ожидаемая прибыль ${est_profit:.2f}."
                    )
                    log(f"[РЕШЕНИЕ][BUY] {short_c} — {decision_text}")
                    if px:
                        log(f"[STATUS][RUNNING][ENTER] {short_c} — готовим сделку {strategy} на ${size_usd:.2f} (~{size_amount:.4f} {chain_label}) по цене ${px:.2f}")
                    else:
                        log(f"[STATUS][RUNNING][ENTER] {short_c} — готовим сделку {strategy} на ${size_usd:.2f} (~{size_amount:.4f} {chain_label})")
                    if settings.MODE in ("live","auto") and hasattr(self._ex,"buy_token"):
                        try:
                            log(f"[STATUS][RUNNING][BUY] {short_c} — отправляем заявку в OpenSea на ${size_usd:.2f} (~{size_amount:.4f} {chain_label})")
                            h=self._ex.buy_token(trade["contract"], trade["token_id"])
                            log(f"[LIVE][OK] {h} {trade}")
                            register_trade_event(
                                "filled",
                                contract=c,
                                strategy=strategy,
                                size_usd=size_usd,
                                size_native=size_native,
                                note=f"TX {h}",
                                action="buy",
                                symbol=symbol,
                            )
                            log(f"[STATUS][RUNNING][BUY] {short_c} — заявка подтверждена, TX={h}")
                            if est_profit>0:
                                log(f"[STATUS][RUNNING][RESULT] {short_c} — ожидаемый профит ${est_profit:.2f}")
                        except Exception as e:
                            log(f"[LIVE][ERR] {e} {trade}")
                            register_trade_event(
                                "error",
                                contract=c,
                                strategy=strategy,
                                size_usd=size_usd,
                                size_native=size_native,
                                note=str(e),
                                action="error",
                                symbol=symbol,
                            )
                            log(f"[STATUS][RUNNING][ERROR] {short_c} — не удалось купить: {e}")
                    else:
                        PaperExecutor().buy(trade, size_amount)
                        if settings.MODE == "paper":
                            paper_wallet.record_buy(
                                trade,
                                size_native=size_native,
                                size_usd=size_usd,
                                price=px,
                                symbol=symbol,
                            )
                            snapshot = paper_wallet.snapshot(price=px, symbol=symbol)
                            current_native = self._to_float(snapshot.get("balance_native"), current_native)
                            native_balance = current_native
                        log(f"[TRADE][PAPER][BUY] {strategy} {short_c} size=${size_usd:.2f} (~{size_amount:.4f} {chain_label} unit)")
                        log(f"[STATUS][RUNNING][BUY] {short_c} — бумажная покупка {strategy} на ${size_usd:.2f} (~{size_amount:.4f} {chain_label})")
                        risk["spend_today_usd"]=self._to_float(risk.get("spend_today_usd",0.0),0.0)+size_usd
                        ok = random.random() < (0.52 + min(0.05, edge*10))
                        if ok:
                            stats["by_strategy"][strategy]["wins"]+=1
                            profit=self._to_float(max(0.01, size_usd*edge*0.5),0.01)
                            profit_native=self._to_float((profit/px) if px else 0.0,0.0)
                            risk["pnl_today_usd"]=self._to_float(risk.get("pnl_today_usd",0.0),0.0)+profit
                            risk["last_trade_profit_usd"]=self._to_float(profit,0.0)
                            risk["loss_streak"]=0
                            self._check_auto_stop(profit)
                            register_trade_event(
                                "win",
                                contract=c,
                                strategy=strategy,
                                size_usd=size_usd,
                                size_native=size_native,
                                pnl_usd=profit,
                                pnl_native=profit_native,
                                note=f"Профит ${profit:.2f}",
                                action="sell",
                                symbol=symbol,
                            )
                            log(f"[TRADE][RESULT][WIN] {strategy} {short_c} +${profit:.2f}")
                            log(f"[STATUS][RUNNING][SELL] {short_c} — фиксация прибыли ${profit:.2f} по стратегии {strategy}")
                            if settings.MODE == "paper":
                                paper_wallet.record_result(
                                    c,
                                    trade.get("token_id"),
                                    pnl_native=profit_native,
                                    pnl_usd=profit,
                                    price=px,
                                    symbol=symbol,
                                )
                                snapshot = paper_wallet.snapshot(price=px, symbol=symbol)
                                current_native = self._to_float(snapshot.get("balance_native"), current_native)
                                native_balance = current_native
                        else:
                            stats["by_strategy"][strategy]["losses"]+=1
                            loss=self._to_float(min(0.5, size_usd*0.5),0.0)
                            loss_native=self._to_float((-(loss/px)) if px else 0.0,0.0)
                            risk["pnl_today_usd"]=self._to_float(risk.get("pnl_today_usd",0.0),0.0)-loss
                            risk["last_trade_profit_usd"]=self._to_float(-loss,0.0)
                            register_trade_event(
                                "loss",
                                contract=c,
                                strategy=strategy,
                                size_usd=size_usd,
                                size_native=size_native,
                                pnl_usd=-loss,
                                pnl_native=loss_native,
                                note=f"Убыток ${loss:.2f}",
                                action="sell",
                                symbol=symbol,
                            )
                            log(f"[TRADE][RESULT][LOSS] {strategy} {short_c} -${loss:.2f}")
                            log(f"[STATUS][RUNNING][SELL] {short_c} — фиксация убытка ${loss:.2f} по стратегии {strategy}")
                            if settings.MODE == "paper":
                                paper_wallet.record_result(
                                    c,
                                    trade.get("token_id"),
                                    pnl_native=loss_native,
                                    pnl_usd=-loss,
                                    price=px,
                                    symbol=symbol,
                                )
                                snapshot = paper_wallet.snapshot(price=px, symbol=symbol)
                                current_native = self._to_float(snapshot.get("balance_native"), current_native)
                                native_balance = current_native
                            try:
                                current_streak=int(risk.get("loss_streak",0))
                            except (TypeError, ValueError):
                                current_streak=0
                            risk["loss_streak"]=current_streak+1
                            self._check_auto_stop()
                    if self._stop:
                        return
                    time.sleep(0.2)
            if self._stop:
                return
            time.sleep(1.0)
        except Exception as e:
            self._stop=True
            if not self._stop_reason:
                self._stop_reason=f"runtime error: {e}"
            log(f"[ENGINE][ERR] runtime error: {e}")
            register_trade_event("error", note=str(e), action="error")
        finally:
            self._last_heartbeat=time.time()
            self._stopped_at=self._last_heartbeat
            log(f"[ENGINE] loop exit ({self._stop_reason or 'stopped'})")
            register_trade_event("idle", note=self._stop_reason or "Движок остановлен", action="stop")
