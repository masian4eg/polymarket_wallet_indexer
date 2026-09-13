"""PostgreSQL helpers."""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.rows import dict_row

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=False)


def init_schema(conn: psycopg.Connection) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def get_sync_state(conn: psycopg.Connection, wallet: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM sync_state WHERE wallet = %s",
            (wallet,),
        )
        return cur.fetchone()


def upsert_sync_state(
    conn: psycopg.Connection, wallet: str, start_block: int, last_block: int
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_state (wallet, start_block, last_indexed_block, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (wallet) DO UPDATE SET
                last_indexed_block = EXCLUDED.last_indexed_block,
                updated_at = NOW()
            """,
            (wallet, start_block, last_block),
        )


def insert_erc20_batch(conn: psycopg.Connection, rows: list[tuple]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO erc20_transfers (
                wallet, token, token_symbol, block_number, tx_hash, log_index,
                from_addr, to_addr, amount, direction
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tx_hash, log_index) DO NOTHING
            """,
            rows,
        )


def insert_erc1155_batch(conn: psycopg.Connection, rows: list[tuple]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO erc1155_transfers (
                wallet, token, block_number, tx_hash, log_index, batch_index,
                operator, from_addr, to_addr, token_id, amount, direction
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tx_hash, log_index, batch_index) DO NOTHING
            """,
            rows,
        )

def recompute_balances(conn: psycopg.Connection, wallet: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM token_balances WHERE wallet = %s", (wallet,))
        cur.execute(
            """
            INSERT INTO token_balances (
                wallet, token, token_kind, token_id, token_symbol, balance_calc
            )
            SELECT
                wallet,
                token,
                'erc20',
                0,
                MAX(token_symbol),
                SUM(
                    CASE direction
                        WHEN 'in' THEN amount
                        WHEN 'out' THEN -amount
                        ELSE 0
                    END
                )::NUMERIC(78,0)
            FROM erc20_transfers
            WHERE wallet = %s
            GROUP BY wallet, token
            """,
            (wallet,),
        )
        cur.execute(
            """
            INSERT INTO token_balances (
                wallet, token, token_kind, token_id, token_symbol, balance_calc
            )
            SELECT
                wallet,
                token,
                'erc1155',
                token_id,
                'CTF',
                SUM(
                    CASE direction
                        WHEN 'in' THEN amount
                        WHEN 'out' THEN -amount
                        ELSE 0
                    END
                )::NUMERIC(78,0)
            FROM erc1155_transfers
            WHERE wallet = %s
            GROUP BY wallet, token, token_id
            """,
            (wallet,),
        )


def list_balances(conn: psycopg.Connection, wallet: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM token_balances
            WHERE wallet = %s
            ORDER BY token_kind, token, token_id
            """,
            (wallet,),
        )
        return cur.fetchall()


def update_chain_balance(
    conn: psycopg.Connection,
    *,
    wallet: str,
    token: str,
    token_id: int,
    balance_chain: int,
    matched: bool,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE token_balances
            SET balance_chain = %s, matched = %s, checked_at = NOW()
            WHERE wallet = %s AND token = %s AND token_id = %s
            """,
            (balance_chain, matched, wallet, token, token_id),
        )


def record_verify_run(
    conn: psycopg.Connection,
    wallet: str,
    erc20_ok: int,
    erc20_fail: int,
    erc1155_ok: int,
    erc1155_fail: int,
    notes: str = "",
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO verify_runs (
                wallet, erc20_ok, erc20_fail, erc1155_ok, erc1155_fail, notes
            ) VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (wallet, erc20_ok, erc20_fail, erc1155_ok, erc1155_fail, notes),
        )
