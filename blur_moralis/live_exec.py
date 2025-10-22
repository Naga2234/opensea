import httpx
from typing import Tuple, Optional
from web3 import Web3
from eth_account import Account
from .config import settings
from .runtime import log

class LiveNotConfigured(Exception): pass

class OpenSeaExecutor:
    BASE = "https://api.opensea.io/api/v2"
    def __init__(self, w3: Web3, address: str, private_key: str):
        if not settings.OPENSEA_API_KEY:
            raise LiveNotConfigured("Missing OPENSEA_API_KEY")
        self.w3=w3
        self.addr=Web3.to_checksum_address(address) if address else Account.from_key(private_key).address
        self.pk=private_key
        self.client=httpx.Client(timeout=30, headers={"X-API-KEY": settings.OPENSEA_API_KEY, "Accept":"application/json"})
        self.chain=(settings.CHAIN or "eth")

    def _chain(self)->str:
        c=(self.chain or 'eth').lower()
        if c in ('eth','ethereum','mainnet'): return 'ethereum'
        if c in ('polygon','matic'): return 'matic'
        return 'ethereum'

    def _gas_params(self)->Tuple[int,int]:
        max_fee=int(Web3.to_wei(float(getattr(settings,"GAS_MAX_FEE_GWEI",25.0)),"gwei"))
        prio=int(Web3.to_wei(float(getattr(settings,"GAS_PRIORITY_GWEI",1.5)),"gwei"))
        return max_fee, prio

    def best_listing(self, contract: str, token_id: str) -> Optional[dict]:
        r=self.client.get(f"{self.BASE}/listings", params={"asset_contract_address":contract, "token_ids": token_id, "limit": 1, "order_by":"eth_price", "order_direction":"asc"})
        r.raise_for_status(); data=r.json()
        orders=(data.get("listings") or data.get("orders") or [])
        return orders[0] if orders else None

    def fulfillment_data(self, order: dict) -> dict:
        payload={"listing": order, "chain": self._chain(), "taker": self.addr}
        r=self.client.post(f"{self.BASE}/listings/fulfillment_data", json=payload)
        r.raise_for_status(); return r.json()

    def buy_token(self, contract: str, token_id: str)->str:
        order=self.best_listing(contract, token_id)
        if not order: raise RuntimeError("No OpenSea listing found for token")
        fd=self.fulfillment_data(order)
        tx=fd.get("transaction") or fd.get("fulfillment_data",{}).get("transaction")
        if not tx or "to" not in tx or "data" not in tx:
            raise RuntimeError("No transaction data from OpenSea")
        max_fee, prio = self._gas_params()
        txd={"from":self.addr,"to":Web3.to_checksum_address(tx["to"]),"data":tx["data"],"value":int(tx.get("value","0")),
             "nonce":self.w3.eth.get_transaction_count(self.addr),"maxFeePerGas":max_fee,"maxPriorityFeePerGas":prio,
             "gas":min(int(tx.get("gas","500000")), int(getattr(settings,"GAS_LIMIT_CAP",500000))),"chainId":self.w3.eth.chain_id,"type":2}
        signed=self.w3.eth.account.sign_transaction(txd, private_key=self.pk)
        h=self.w3.eth.send_raw_transaction(signed.rawTransaction).hex()
        log(f"[LIVE][TX][OS] {h} -> {txd['to']} val={txd['value']}"); return h
