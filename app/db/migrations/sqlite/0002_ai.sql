-- The optional AI layer's own record (SQLite).
--
-- One table does two jobs, because they are the same row seen twice. It is the
-- cache - a reply whose prompt hash is already present is returned instead of
-- being paid for again, which is what makes re-uploading the same photograph
-- free - and it is the audit trail, so a report can state which model described
-- an image and when.
--
-- ``cached`` marks a row written from a cache hit. Such a row costs nothing, so
-- the hourly budget counts rows where it is 0.
CREATE TABLE IF NOT EXISTS ai_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    text TEXT NOT NULL,
    cached INTEGER DEFAULT 0,
    ms INTEGER DEFAULT 0,
    usage_json TEXT,
    created_at TEXT NOT NULL
);

-- The hash is the cache lookup, so it is the one index that matters.
CREATE INDEX IF NOT EXISTS idx_ai_calls_hash ON ai_calls(prompt_hash);
-- The budget counts rows since the top of the hour, and the ledger shows one
-- product's history, which is the other two access patterns there are.
CREATE INDEX IF NOT EXISTS idx_ai_calls_created ON ai_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_calls_product ON ai_calls(product_id, id);
