"""Polygon Polymarket contracts and event topic hashes."""

from __future__ import annotations

from eth_utils import keccak

# Wallet under reconstruction
DEFAULT_WALLET = "0x46b353667fd7d846af3bbeda6584b0e5b883d3de"

# --- Core Polymarket / CTF (Polygon mainnet) ---
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# Collateral (V1 USDC.e + V2 pUSD)
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"

# CTF deployment ≈ early Polymarket on Polygon; safe lower bound for wallet scans
DEFAULT_START_BLOCK = 27_065_000

ERC20_TOKENS = {
    USDC_E.lower(): {"symbol": "USDC.e", "decimals": 6},
    PUSD.lower(): {"symbol": "pUSD", "decimals": 6},
}

CTF_TOKENS = {CTF.lower(): {"symbol": "CTF", "decimals": 6}}


def _topic(sig: str) -> str:
    return "0x" + keccak(sig.encode("ascii")).hex()


TOPIC_ERC20_TRANSFER = _topic("Transfer(address,address,uint256)")
TOPIC_TRANSFER_SINGLE = _topic(
    "TransferSingle(address,address,address,uint256,uint256)"
)
TOPIC_TRANSFER_BATCH = _topic(
    "TransferBatch(address,address,address,uint256[],uint256[])"
)


def addr_topic(address: str) -> str:
    """Pad address to 32-byte topic."""
    a = address.lower().removeprefix("0x")
    if len(a) != 40:
        raise ValueError(f"bad address: {address}")
    return "0x" + ("0" * 24) + a


def normalize_addr(address: str) -> str:
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"expected 0x + 40 hex, got {address!r}")
    return address.lower()
