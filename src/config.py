"""Configuration from env / CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .contracts import DEFAULT_START_BLOCK, DEFAULT_WALLET, normalize_addr

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    rpc_url: str
    database_url: str
    wallet: str
    start_block: int
    chunk_size: int
    confirmations: int
    request_timeout: float
    max_retries: int


def load_settings(
    *,
    wallet: str | None = None,
    rpc_url: str | None = None,
    database_url: str | None = None,
    start_block: int | None = None,
) -> Settings:
    rpc = rpc_url or os.getenv("POLYGON_RPC_URL") or os.getenv("RPC_URL")
    if not rpc:
        raise SystemExit(
            "Set POLYGON_RPC_URL (Alchemy/QuickNode/Infura Polygon endpoint). "
            "Public RPCs usually throttle eth_getLogs too hard for a full rebuild."
        )
    db = database_url or os.getenv("DATABASE_URL")
    if not db:
        raise SystemExit(
            "Set DATABASE_URL, e.g. postgresql://postgres:postgres@127.0.0.1:5432/polymarket"
        )
    w = normalize_addr(wallet or os.getenv("WALLET", DEFAULT_WALLET))
    sb = start_block
    if sb is None:
        sb = int(os.getenv("START_BLOCK", str(DEFAULT_START_BLOCK)))
    return Settings(
        rpc_url=rpc,
        database_url=db,
        wallet=w,
        start_block=sb,
        chunk_size=int(os.getenv("CHUNK_SIZE", "2000")),
        confirmations=int(os.getenv("CONFIRMATIONS", "5")),
        request_timeout=float(os.getenv("RPC_TIMEOUT", "60")),
        max_retries=int(os.getenv("RPC_RETRIES", "8")),
    )
