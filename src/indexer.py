"""Index wallet Transfer events from Polygon into PostgreSQL."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from tqdm import tqdm

from . import db as database
from .contracts import (
    CTF,
    CTF_TOKENS,
    ERC20_TOKENS,
    PUSD,
    TOPIC_ERC20_TRANSFER,
    TOPIC_TRANSFER_BATCH,
    TOPIC_TRANSFER_SINGLE,
    USDC_E,
    normalize_addr,
)
from .rpc import (
    PolygonRpc,
    RpcError,
    addr_topic,
    decode_erc20_transfer,
    decode_transfer_batch,
    decode_transfer_single,
    direction_for,
    erc1155_filters,
    erc20_filters,
)

MAX_RANGE = 100_000  # Tenderly/Pocket archive; publicnode often caps ~10k


def find_earliest_rpc_block(rpc: PolygonRpc, tip: int, wallet: str | None = None) -> int:
    """Binary-search the oldest block this RPC still serves for eth_getLogs."""
    # Use a wallet-scoped topic so the response stays tiny (never "too many results").
    wtopic = addr_topic(wallet) if wallet else None
    if wtopic:
        probe_topics = [TOPIC_TRANSFER_SINGLE, None, None, wtopic]
    else:
        probe_topics = [TOPIC_TRANSFER_SINGLE, None, None, None]
    lo, hi = 1, tip
    best = tip
    while lo <= hi:
        mid = (lo + hi) // 2
        end = min(tip, mid + MAX_RANGE - 1)
        try:
            rpc.get_logs(
                address=CTF,
                topics=probe_topics,
                from_block=mid,
                to_block=end,
            )
            best = mid
            hi = mid - 1
        except RpcError as e:
            msg = str(e).lower()
            if "prun" in msg or "history" in msg or "not available" in msg:
                lo = mid + 1
            elif "maximum block" in msg or "block range" in msg or "too many" in msg:
                best = mid
                hi = mid - 1
            else:
                lo = mid + 1
    return best


def _decode_log_rows(wallet: str, log: dict) -> tuple[list[tuple], list[tuple]]:
    """Decode one RPC log into erc20 / erc1155 row tuples (no DB I/O)."""
    erc20_rows: list[tuple] = []
    erc1155_rows: list[tuple] = []
    address = normalize_addr(log["address"])
    topics = [t.lower() if isinstance(t, str) else ("0x" + t.hex()) for t in log["topics"]]
    topic0 = topics[0]
    block_number = int(log["blockNumber"], 16)
    tx_hash = log["transactionHash"]
    log_index = int(log["logIndex"], 16)

    if topic0 == TOPIC_ERC20_TRANSFER.lower() and address in ERC20_TOKENS:
        d = decode_erc20_transfer(log)
        if wallet not in (d["from"], d["to"]):
            return erc20_rows, erc1155_rows
        meta = ERC20_TOKENS[address]
        erc20_rows.append(
            (
                wallet,
                address,
                meta["symbol"],
                block_number,
                tx_hash,
                log_index,
                d["from"],
                d["to"],
                d["amount"],
                direction_for(wallet, d["from"], d["to"]),
            )
        )
        return erc20_rows, erc1155_rows

    if address not in CTF_TOKENS:
        return erc20_rows, erc1155_rows

    if topic0 == TOPIC_TRANSFER_SINGLE.lower():
        d = decode_transfer_single(log)
        if wallet not in (d["from"], d["to"]):
            return erc20_rows, erc1155_rows
        erc1155_rows.append(
            (
                wallet,
                address,
                block_number,
                tx_hash,
                log_index,
                0,
                d["operator"],
                d["from"],
                d["to"],
                d["token_id"],
                d["amount"],
                direction_for(wallet, d["from"], d["to"]),
            )
        )
        return erc20_rows, erc1155_rows

    if topic0 == TOPIC_TRANSFER_BATCH.lower():
        for d in decode_transfer_batch(log):
            if wallet not in (d["from"], d["to"]):
                continue
            erc1155_rows.append(
                (
                    wallet,
                    address,
                    block_number,
                    tx_hash,
                    log_index,
                    d["batch_index"],
                    d["operator"],
                    d["from"],
                    d["to"],
                    d["token_id"],
                    d["amount"],
                    direction_for(wallet, d["from"], d["to"]),
                )
            )
    return erc20_rows, erc1155_rows


def _fetch_chunk(
    rpc: PolygonRpc,
    filters: list[tuple[str, list]],
    from_block: int,
    to_block: int,
    chunk_size: int,
) -> list[dict]:
    """Fetch logs for all filters; auto-split range on RPC range errors."""
    if from_block > to_block:
        return []

    chunk_size = min(chunk_size, MAX_RANGE)
    collected: list[dict] = []
    stack = [(from_block, to_block)]
    while stack:
        a, b = stack.pop()
        if b - a + 1 > chunk_size:
            mid = (a + b) // 2
            stack.append((mid + 1, b))
            stack.append((a, mid))
            continue
        try:
            def one(filt: tuple[str, list]) -> list[dict]:
                address, topics = filt
                return rpc.get_logs(
                    address=address,
                    topics=topics,
                    from_block=a,
                    to_block=b,
                )

            # 4 workers: enough parallelism without hammering public Tenderly into 429/backoff
            with ThreadPoolExecutor(max_workers=min(4, len(filters))) as pool:
                futs = [pool.submit(one, f) for f in filters]
                for fut in as_completed(futs):
                    collected.extend(fut.result())
        except RpcError as e:
            msg = str(e).lower()
            if "prun" in msg or "history" in msg:
                # Entire window unavailable — skip (caller should start later).
                raise
            if a == b:
                raise
            mid = (a + b) // 2
            stack.append((mid + 1, b))
            stack.append((a, mid))

    seen: set[tuple[str, int]] = set()
    unique: list[dict] = []
    for log in collected:
        key = (log["transactionHash"], int(log["logIndex"], 16))
        if key in seen:
            continue
        seen.add(key)
        unique.append(log)
    unique.sort(key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16)))
    return unique


def build_filters(wallet: str) -> list[tuple[str, list]]:
    # CTF first: Polymarket positions dominate; avoids burning RPC on empty USDC ranges.
    filters: list[tuple[str, list]] = []
    filters.extend(erc1155_filters(wallet, CTF))
    for token in (USDC_E, PUSD):
        filters.extend(erc20_filters(wallet, token))
    return filters


def build_discovery_filters(wallet: str) -> list[tuple[str, list]]:
    """Faster existence probes — CTF only (proxy wallets almost always touch CTF first)."""
    return erc1155_filters(wallet, CTF)


def _any_logs(
    rpc: PolygonRpc,
    filters: list[tuple[str, list]],
    from_block: int,
    to_block: int,
    chunk_size: int = 5_000,
    *,
    reverse: bool = True,
) -> bool:
    """Return True as soon as any matching log is found."""
    if from_block > to_block:
        return False

    if reverse:
        end = to_block
        while end >= from_block:
            start = max(from_block, end - chunk_size + 1)
            for address, topics in filters:
                try:
                    logs = rpc.get_logs(
                        address=address,
                        topics=topics,
                        from_block=start,
                        to_block=end,
                    )
                except RpcError:
                    if start == end:
                        raise
                    mid = (start + end) // 2
                    if _any_logs(rpc, filters, mid + 1, end, chunk_size, reverse=True):
                        return True
                    return _any_logs(rpc, filters, start, mid, chunk_size, reverse=True)
                if logs:
                    return True
            end = start - 1
        return False

    cursor = from_block
    while cursor <= to_block:
        end = min(cursor + chunk_size - 1, to_block)
        for address, topics in filters:
            try:
                logs = rpc.get_logs(
                    address=address,
                    topics=topics,
                    from_block=cursor,
                    to_block=end,
                )
            except RpcError:
                if cursor == end:
                    raise
                mid = (cursor + end) // 2
                if _any_logs(rpc, filters, cursor, mid, chunk_size, reverse=False):
                    return True
                return _any_logs(rpc, filters, mid + 1, end, chunk_size, reverse=False)
            if logs:
                return True
        cursor = end + 1
    return False


def find_first_activity(
    rpc: PolygonRpc,
    wallet: str,
    *,
    start_block: int,
    tip: int,
    probe_size: int = 10_000,
) -> int:
    """Walk backward in RPC-sized windows until the first empty gap, then refine."""
    filters = build_discovery_filters(wallet)
    probe_size = min(probe_size, 10_000)  # publicnode hard cap

    cursor = tip
    earliest_hit = None
    while cursor >= start_block:
        a = max(start_block, cursor - probe_size + 1)
        hit = _any_logs(rpc, filters, a, cursor, chunk_size=probe_size)
        if hit:
            earliest_hit = a
            if earliest_hit % (probe_size * 50) < probe_size:
                print(f"  still active near {a}", flush=True)
        elif earliest_hit is not None:
            break
        cursor = a - 1

    if earliest_hit is None:
        filters = build_filters(wallet)
        cursor = tip
        while cursor >= start_block:
            a = max(start_block, cursor - probe_size + 1)
            if _any_logs(rpc, filters, a, cursor, chunk_size=probe_size):
                earliest_hit = a
            elif earliest_hit is not None:
                break
            cursor = a - 1
        if earliest_hit is None:
            return tip

    lo = max(start_block, earliest_hit)
    hi = min(tip, earliest_hit + probe_size - 1)
    # Ensure lo is truly the first: step back one more empty-confirmed window already done.
    # Refine inside the earliest hit window.
    print(f"  refine {lo}-{hi}", flush=True)
    while lo < hi:
        mid = (lo + hi) // 2
        if _any_logs(rpc, filters, lo, mid, chunk_size=probe_size):
            hi = mid
        else:
            lo = mid + 1
    return lo


def index_wallet(
    conn,
    rpc: PolygonRpc,
    wallet: str,
    *,
    start_block: int,
    chunk_size: int = 2000,
    confirmations: int = 5,
    discover_start: bool = True,
    probe_history: bool = True,
) -> int:
    tip = rpc.block_number() - confirmations
    if tip < start_block:
        raise SystemExit(f"chain tip {tip} < start_block {start_block}")

    if probe_history:
        print("Detecting earliest eth_getLogs block on this RPC…")
        earliest_rpc = find_earliest_rpc_block(rpc, tip, wallet)
        print(f"RPC history starts at block {earliest_rpc}")
        if earliest_rpc > start_block:
            print(
                "WARNING: RPC is pruned. Exact balance match needs an archive Polygon endpoint "
                f"(Alchemy/QuickNode/etc.). Indexing from {earliest_rpc} instead of {start_block}."
            )
            start_block = earliest_rpc

    state = database.get_sync_state(conn, wallet)
    from_block = start_block
    if state and state["last_indexed_block"] >= start_block:
        from_block = int(state["last_indexed_block"]) + 1
        discover_start = False
    elif discover_start:
        print("Discovering first on-chain activity in available history…")
        first = find_first_activity(rpc, wallet, start_block=start_block, tip=tip)
        print(f"First activity at block {first}")
        from_block = first
        start_block = first

    if from_block > tip:
        print(f"Already indexed through {tip}")
        return tip

    filters = build_filters(wallet)
    total_blocks = tip - from_block + 1
    pbar = tqdm(total=total_blocks, unit="blk", desc="indexing")
    print(
        f"Resuming {from_block} -> {tip} ({total_blocks} blocks), chunk={min(chunk_size, MAX_RANGE)}",
        flush=True,
    )

    cursor = from_block
    while cursor <= tip:
        end = min(cursor + min(chunk_size, MAX_RANGE) - 1, tip)
        logs = _fetch_chunk(rpc, filters, cursor, end, min(chunk_size, MAX_RANGE))
        erc20_rows: list[tuple] = []
        erc1155_rows: list[tuple] = []
        for log in logs:
            e20, e1155 = _decode_log_rows(wallet, log)
            erc20_rows.extend(e20)
            erc1155_rows.extend(e1155)
        # Skip raw_logs: doubles write cost and is not needed for balance math.
        database.insert_erc20_batch(conn, erc20_rows)
        database.insert_erc1155_batch(conn, erc1155_rows)
        database.upsert_sync_state(conn, wallet, start_block, end)
        conn.commit()
        pbar.update(end - cursor + 1)
        pbar.set_postfix(logs=len(logs), e20=len(erc20_rows), e1155=len(erc1155_rows))
        cursor = end + 1

    pbar.close()
    database.recompute_balances(conn, wallet)
    conn.commit()
    print(f"Indexed {wallet} through block {tip} ({datetime.now(timezone.utc).isoformat()})")
    return tip
