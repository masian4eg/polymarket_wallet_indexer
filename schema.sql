-- Polymarket wallet indexer schema (Polygon / CTF)
-- Balances are derived only from on-chain Transfer* events.

CREATE TABLE IF NOT EXISTS sync_state (
    wallet            TEXT PRIMARY KEY,
    start_block       BIGINT NOT NULL,
    last_indexed_block BIGINT NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_logs (
    id              BIGSERIAL PRIMARY KEY,
    wallet          TEXT NOT NULL,
    address         TEXT NOT NULL,
    topic0          TEXT NOT NULL,
    block_number    BIGINT NOT NULL,
    block_hash      TEXT NOT NULL,
    tx_hash         TEXT NOT NULL,
    log_index       INT NOT NULL,
    data            TEXT NOT NULL,
    topics          JSONB NOT NULL,
    UNIQUE (tx_hash, log_index)
);

CREATE INDEX IF NOT EXISTS idx_raw_logs_wallet_block
    ON raw_logs (wallet, block_number);

CREATE TABLE IF NOT EXISTS erc20_transfers (
    id              BIGSERIAL PRIMARY KEY,
    wallet          TEXT NOT NULL,
    token           TEXT NOT NULL,
    token_symbol    TEXT NOT NULL,
    block_number    BIGINT NOT NULL,
    block_time      TIMESTAMPTZ,
    tx_hash         TEXT NOT NULL,
    log_index       INT NOT NULL,
    from_addr       TEXT NOT NULL,
    to_addr         TEXT NOT NULL,
    amount          NUMERIC(78, 0) NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('in', 'out', 'self')),
    UNIQUE (tx_hash, log_index)
);

CREATE INDEX IF NOT EXISTS idx_erc20_wallet_token
    ON erc20_transfers (wallet, token);

CREATE TABLE IF NOT EXISTS erc1155_transfers (
    id              BIGSERIAL PRIMARY KEY,
    wallet          TEXT NOT NULL,
    token           TEXT NOT NULL,
    block_number    BIGINT NOT NULL,
    block_time      TIMESTAMPTZ,
    tx_hash         TEXT NOT NULL,
    log_index       INT NOT NULL,
    batch_index     INT NOT NULL DEFAULT 0,
    operator        TEXT,
    from_addr       TEXT NOT NULL,
    to_addr         TEXT NOT NULL,
    token_id        NUMERIC(78, 0) NOT NULL,
    amount          NUMERIC(78, 0) NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('in', 'out', 'self')),
    UNIQUE (tx_hash, log_index, batch_index)
);

CREATE INDEX IF NOT EXISTS idx_erc1155_wallet_token_id
    ON erc1155_transfers (wallet, token, token_id);

-- Computed from transfer tables; verified against eth_call balanceOf.
CREATE TABLE IF NOT EXISTS token_balances (
    wallet          TEXT NOT NULL,
    token           TEXT NOT NULL,
    token_kind      TEXT NOT NULL CHECK (token_kind IN ('erc20', 'erc1155')),
    token_id        NUMERIC(78, 0) NOT NULL DEFAULT 0,
    token_symbol    TEXT,
    balance_calc    NUMERIC(78, 0) NOT NULL,
    balance_chain   NUMERIC(78, 0),
    matched         BOOLEAN,
    checked_at      TIMESTAMPTZ,
    PRIMARY KEY (wallet, token, token_id)
);

CREATE TABLE IF NOT EXISTS verify_runs (
    id              BIGSERIAL PRIMARY KEY,
    wallet          TEXT NOT NULL,
    ran_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    erc20_ok        INT NOT NULL DEFAULT 0,
    erc20_fail      INT NOT NULL DEFAULT 0,
    erc1155_ok      INT NOT NULL DEFAULT 0,
    erc1155_fail    INT NOT NULL DEFAULT 0,
    notes           TEXT
);
