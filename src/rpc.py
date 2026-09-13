"""Thin JSON-RPC client with chunked getLogs and retries."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import requests
from eth_abi import decode, encode
from web3 import Web3

from .contracts import (
    TOPIC_ERC20_TRANSFER,
    TOPIC_TRANSFER_BATCH,
    TOPIC_TRANSFER_SINGLE,
    addr_topic,
    normalize_addr,
)


class RpcError(RuntimeError):
    pass


class PolygonRpc:
    def __init__(self, url: str, *, timeout: float = 60, max_retries: int = 8):
        self.url = url
        self.timeout = timeout
        self.max_retries = max_retries
        self._id = 0
        self._lock = threading.Lock()

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        with self._lock:
            self._id += 1
            req_id = self._id
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or [],
        }
        delay = 0.5
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                r = requests.post(self.url, json=payload, timeout=self.timeout)
                if r.status_code in (429, 502, 503, 504):
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                r.raise_for_status()
                body = r.json()
                if "error" in body:
                    msg = body["error"]
                    text = json.dumps(msg) if not isinstance(msg, str) else msg
                    if any(
                        s in text.lower()
                        for s in ("too many", "block range", "query returned more", "limit", "maximum block")
                    ):
                        raise RpcError(text)
                    if attempt + 1 < self.max_retries:
                        time.sleep(delay)
                        delay = min(delay * 2, 30)
                        last_err = RpcError(text)
                        continue
                    raise RpcError(text)
                return body["result"]
            except (requests.RequestException, ValueError, KeyError) as e:
                last_err = e
                time.sleep(delay)
                delay = min(delay * 2, 30)
        raise RpcError(f"{method} failed after retries: {last_err}")

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber"), 16)

    def get_logs(
        self,
        *,
        address: str | list[str],
        topics: list[Any],
        from_block: int,
        to_block: int,
    ) -> list[dict]:
        params = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": address,
            "topics": topics,
        }
        return self.call("eth_getLogs", [params])

    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        return self.call("eth_call", [{"to": to, "data": data}, block])

    def balance_of_erc20(self, token: str, owner: str, block: str | int = "latest") -> int:
        # balanceOf(address)
        data = "0x70a08231" + owner.lower().removeprefix("0x").zfill(64)
        blk = hex(block) if isinstance(block, int) else block
        raw = self.eth_call(Web3.to_checksum_address(token), data, blk)
        return int(raw, 16) if raw and raw != "0x" else 0

    def balance_of_erc1155(
        self, token: str, owner: str, token_id: int, block: str | int = "latest"
    ) -> int:
        # balanceOf(address,uint256)
        data = (
            "0x00fdd58e"
            + owner.lower().removeprefix("0x").zfill(64)
            + format(token_id, "064x")
        )
        blk = hex(block) if isinstance(block, int) else block
        raw = self.eth_call(Web3.to_checksum_address(token), data, blk)
        return int(raw, 16) if raw and raw != "0x" else 0

    def balance_of_batch_erc1155(
        self,
        token: str,
        owner: str,
        token_ids: list[int],
        block: str | int = "latest",
    ) -> list[int]:
        """ERC-1155 balanceOfBatch(address[],uint256[])."""
        if not token_ids:
            return []
        owners = [owner] * len(token_ids)
        payload = encode(["address[]", "uint256[]"], [owners, token_ids])
        data = "0x4e1273f4" + payload.hex()
        blk = hex(block) if isinstance(block, int) else block
        raw = self.eth_call(Web3.to_checksum_address(token), data, blk)
        if not raw or raw in ("0x", "0x0"):
            return [0] * len(token_ids)
        decoded = decode(["uint256[]"], bytes.fromhex(raw[2:]))[0]
        return [int(x) for x in decoded]


def decode_erc20_transfer(log: dict) -> dict:
    topics = log["topics"]
    frm = "0x" + topics[1][-40:]
    to = "0x" + topics[2][-40:]
    amount = int(log["data"], 16) if log["data"] not in ("0x", "") else 0
    return {
        "from": normalize_addr(frm),
        "to": normalize_addr(to),
        "amount": amount,
    }


def decode_transfer_single(log: dict) -> dict:
    topics = log["topics"]
    operator = "0x" + topics[1][-40:]
    frm = "0x" + topics[2][-40:]
    to = "0x" + topics[3][-40:]
    token_id, amount = decode(["uint256", "uint256"], bytes.fromhex(log["data"][2:]))
    return {
        "operator": normalize_addr(operator),
        "from": normalize_addr(frm),
        "to": normalize_addr(to),
        "token_id": int(token_id),
        "amount": int(amount),
    }


def decode_transfer_batch(log: dict) -> list[dict]:
    topics = log["topics"]
    operator = "0x" + topics[1][-40:]
    frm = "0x" + topics[2][-40:]
    to = "0x" + topics[3][-40:]
    ids, values = decode(
        ["uint256[]", "uint256[]"], bytes.fromhex(log["data"][2:])
    )
    out = []
    for i, (tid, amt) in enumerate(zip(ids, values)):
        out.append(
            {
                "operator": normalize_addr(operator),
                "from": normalize_addr(frm),
                "to": normalize_addr(to),
                "token_id": int(tid),
                "amount": int(amt),
                "batch_index": i,
            }
        )
    return out


def direction_for(wallet: str, frm: str, to: str) -> str:
    w = wallet.lower()
    if frm == w and to == w:
        return "self"
    if to == w:
        return "in"
    if frm == w:
        return "out"
    raise ValueError("log does not involve wallet")


def erc20_filters(wallet: str, token: str) -> list[tuple[str, list]]:
    """Two filters: transfers FROM wallet and TO wallet."""
    w = addr_topic(wallet)
    t0 = TOPIC_ERC20_TRANSFER
    return [
        (token, [t0, w, None]),
        (token, [t0, None, w]),
    ]


def erc1155_filters(wallet: str, token: str) -> list[tuple[str, list]]:
    w = addr_topic(wallet)
    return [
        (token, [TOPIC_TRANSFER_SINGLE, None, w, None]),
        (token, [TOPIC_TRANSFER_SINGLE, None, None, w]),
        (token, [TOPIC_TRANSFER_BATCH, None, w, None]),
        (token, [TOPIC_TRANSFER_BATCH, None, None, w]),
    ]
