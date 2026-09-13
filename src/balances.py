"""Recompute balances and verify against live eth_call / balanceOfBatch."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from . import db as database
from .rpc import PolygonRpc


def verify_balances(
    conn,
    rpc: PolygonRpc,
    wallet: str,
    *,
    only_nonzero: bool = False,
    workers: int = 8,
    batch_size: int = 200,
    at_block: int | None = None,
) -> dict:
    database.recompute_balances(conn, wallet)
    conn.commit()

    if at_block is None:
        st = database.get_sync_state(conn, wallet)
        at_block = int(st["last_indexed_block"]) if st else rpc.block_number()
    print(f"Verifying against chain state at block {at_block}", flush=True)

    rows = database.list_balances(conn, wallet)
    if only_nonzero:
        rows = [r for r in rows if int(r["balance_calc"]) != 0]

    erc20_rows = [r for r in rows if r["token_kind"] == "erc20"]
    erc1155_rows = [r for r in rows if r["token_kind"] == "erc1155"]

    erc20_ok = erc20_fail = erc1155_ok = erc1155_fail = 0
    mismatches: list[str] = []

    # ERC-20: few tokens
    for row in erc20_rows:
        calc = int(row["balance_calc"])
        chain = rpc.balance_of_erc20(row["token"], wallet, at_block)
        matched = calc == chain
        if matched:
            erc20_ok += 1
        else:
            erc20_fail += 1
            mismatches.append(
                f"ERC20 {row['token_symbol']} calc={calc} chain={chain}"
            )
        database.update_chain_balance(
            conn,
            wallet=wallet,
            token=row["token"],
            token_id=int(row["token_id"]),
            balance_chain=chain,
            matched=matched,
        )

    # ERC-1155: balanceOfBatch in chunks (parallel across chunks)
    def check_chunk(chunk: list[dict]) -> list[tuple[dict, int, bool]]:
        if not chunk:
            return []
        token = chunk[0]["token"]
        ids = [int(r["token_id"]) for r in chunk]
        try:
            balances = rpc.balance_of_batch_erc1155(token, wallet, ids, at_block)
        except Exception:
            balances = [
                rpc.balance_of_erc1155(token, wallet, i, at_block) for i in ids
            ]
        out = []
        for row, chain in zip(chunk, balances):
            calc = int(row["balance_calc"])
            out.append((row, chain, calc == chain))
        return out

    chunks = [
        erc1155_rows[i : i + batch_size]
        for i in range(0, len(erc1155_rows), batch_size)
    ]
    print(f"Verifying {len(erc1155_rows)} ERC1155 in {len(chunks)} batches…", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(check_chunk, c) for c in chunks]
        done = 0
        for fut in as_completed(futs):
            for row, chain, matched in fut.result():
                if matched:
                    erc1155_ok += 1
                else:
                    erc1155_fail += 1
                    if len(mismatches) < 50:
                        mismatches.append(
                            f"ERC1155 id={int(row['token_id'])} "
                            f"calc={int(row['balance_calc'])} chain={chain}"
                        )
                database.update_chain_balance(
                    conn,
                    wallet=wallet,
                    token=row["token"],
                    token_id=int(row["token_id"]),
                    balance_chain=chain,
                    matched=matched,
                )
            done += 1
            if done % 50 == 0 or done == len(chunks):
                print(f"  batches {done}/{len(chunks)}", flush=True)

    notes = "; ".join(mismatches[:20])
    if len(mismatches) > 20:
        notes += f" … (+{len(mismatches) - 20} more)"
    database.record_verify_run(
        conn, wallet, erc20_ok, erc20_fail, erc1155_ok, erc1155_fail, notes
    )
    conn.commit()

    return {
        "erc20_ok": erc20_ok,
        "erc20_fail": erc20_fail,
        "erc1155_ok": erc1155_ok,
        "erc1155_fail": erc1155_fail,
        "mismatches": mismatches,
        "positions": len(rows),
        "at_block": at_block,
    }


def print_balance_report(conn, wallet: str, *, only_nonzero: bool = True) -> None:
    rows = database.list_balances(conn, wallet)
    print(f"\nBalances for {wallet}")
    print("-" * 88)
    print(
        f"{'kind':8} {'symbol':8} {'token_id':>24} {'calc':>18} {'chain':>18} {'ok':>4}"
    )
    shown = 0
    for row in rows:
        calc = int(row["balance_calc"])
        if only_nonzero and calc == 0 and (row["balance_chain"] in (None, 0)):
            continue
        chain = row["balance_chain"]
        chain_s = str(int(chain)) if chain is not None else "-"
        ok = "" if row["matched"] is None else ("YES" if row["matched"] else "NO")
        tid = str(int(row["token_id"])) if row["token_kind"] == "erc1155" else "-"
        print(
            f"{row['token_kind']:8} {(row['token_symbol'] or ''):8} {tid:>24} "
            f"{calc:>18} {chain_s:>18} {ok:>4}"
        )
        shown += 1
        if shown >= 40:
            print(f"... ({len(rows)} total rows; showing first {shown} nonzero)")
            break
