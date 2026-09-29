-- FormuSense initial schema (PostgreSQL).
-- Same tables as the SQLite schema, with SERIAL keys. Kept as a separate file so
-- each dialect's DDL stays plain SQL that a DBA can read.
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    brief_json TEXT NOT NULL,
    plant_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS formulations (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    source TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(product_id, version)
);
CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    formulation_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    objective REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trials (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL,
    formulation_version INTEGER NOT NULL,
    label TEXT,
    measurements_json TEXT NOT NULL,
    sensory_json TEXT,
    process_json TEXT,
    batch_size_kg REAL,
    operator TEXT,
    trial_date TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL,
    trial_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnoses (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL,
    trial_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    accepted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id SERIAL PRIMARY KEY,
    product_id INTEGER,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmarks (
    id SERIAL PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_formulations_product ON formulations(product_id, version);
CREATE INDEX IF NOT EXISTS idx_predictions_formulation ON predictions(formulation_id);
CREATE INDEX IF NOT EXISTS idx_trials_product ON trials(product_id, formulation_version);
CREATE INDEX IF NOT EXISTS idx_analyses_trial ON analyses(trial_id);
CREATE INDEX IF NOT EXISTS idx_diagnoses_trial ON diagnoses(trial_id);
CREATE INDEX IF NOT EXISTS idx_plans_product ON plans(product_id);
CREATE INDEX IF NOT EXISTS idx_ledger_product ON ledger(product_id, id);
