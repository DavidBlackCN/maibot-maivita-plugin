ALTER TABLE life_state ADD COLUMN sleepiness INTEGER CHECK(sleepiness IS NULL OR sleepiness BETWEEN 0 AND 10000);
CREATE TABLE activity_details (
    activity_id TEXT PRIMARY KEY REFERENCES activities(activity_id),
    details_json TEXT NOT NULL,
    next_step TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source='character_simulation'),
    status TEXT NOT NULL CHECK(status IN ('active','ended','abandoned','archived')),
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE model_usage (
    call_id TEXT PRIMARY KEY,
    task_kind TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    elapsed_seconds REAL NOT NULL,
    status TEXT NOT NULL,
    tokens_json TEXT
);
CREATE TABLE frequency_claims (
    stream_id TEXT PRIMARY KEY,
    original REAL NOT NULL,
    last_written REAL NOT NULL,
    status TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE webui_jobs (
    request_id TEXT PRIMARY KEY,
    args_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    created_at_ms INTEGER NOT NULL
);
