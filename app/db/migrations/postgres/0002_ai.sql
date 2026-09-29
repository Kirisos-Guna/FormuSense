-- The optional AI layer's own record (PostgreSQL).
-- Same table as the SQLite schema, with a SERIAL key.
CREATE TABLE IF NOT EXISTS ai_calls (
    id SERIAL PRIMARY KEY,
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

CREATE INDEX IF NOT EXISTS idx_ai_calls_hash ON ai_calls(prompt_hash);
CREATE INDEX IF NOT EXISTS idx_ai_calls_created ON ai_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_calls_product ON ai_calls(product_id, id);
