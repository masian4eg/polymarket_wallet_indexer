"""CLI: index Polymarket wallet history from Polygon RPC into PostgreSQL."""

from __future__ import annotations

import argparse
import os
import sys

from .balances import print_balance_report, verify_balances
from .config import load_settings
from .contracts import DEFAULT_WALLET
from . import db as database
from .indexer import index_wallet
from .rpc import PolygonRpc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Rebuild Polymarket wallet history from Polygon eth_getLogs "
            "(USDC.e + pUSD + CTF ERC1155) and verify balances via eth_call."
        )
    )
    p.add_argument("command", choices=["init-db", "index", "verify", "report", "run"])
    p.add_argument("--wallet", default=DEFAULT_WALLET)
    p.add_argument("--rpc", default=None, help="POLYGON_RPC_URL override")
    p.add_argument("--database-url", default=None)
    p.add_argument("--start-block", type=int, default=None)
    p.add_argument("--all-zero", action="store_true", help="Include zero balances in report")
    p.add_argument(
        "--no-discover",
        action="store_true",
        help="Do not binary-/back-search first activity; use --start-block as-is",
    )
    p.add_argument(
        "--skip-history-probe",
        action="store_true",
        help="Skip binary-search for RPC prune depth (use --start-block)",
    )
    args = p.parse_args(argv)

    if args.command == "init-db":
        db_url = args.database_url or os.getenv("DATABASE_URL")
        if not db_url:
            raise SystemExit("Set DATABASE_URL")
        with database.connect(db_url) as conn:
            database.init_schema(conn)
        print("Schema applied.")
        return 0

    settings = load_settings(
        wallet=args.wallet,
        rpc_url=args.rpc,
        database_url=args.database_url,
        start_block=args.start_block,
    )
    rpc = PolygonRpc(
        settings.rpc_url,
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
    )

    with database.connect(settings.database_url) as conn:
        if args.command in ("index", "run", "verify", "report"):
            database.init_schema(conn)

        if args.command in ("index", "run"):
            index_wallet(
                conn,
                rpc,
                settings.wallet,
                start_block=settings.start_block,
                chunk_size=settings.chunk_size,
                confirmations=settings.confirmations,
                discover_start=not args.no_discover,
                probe_history=not args.skip_history_probe,
            )

        if args.command in ("verify", "run"):
            result = verify_balances(conn, rpc, settings.wallet)
            print(
                f"Verify: erc20 {result['erc20_ok']} ok / {result['erc20_fail']} fail; "
                f"erc1155 {result['erc1155_ok']} ok / {result['erc1155_fail']} fail; "
                f"rows={result['positions']}"
            )
            if result["mismatches"]:
                print("MISMATCHES:")
                for m in result["mismatches"][:50]:
                    print(" ", m)
                return 2

        if args.command in ("report", "run", "verify"):
            print_balance_report(
                conn, settings.wallet, only_nonzero=not args.all_zero
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
