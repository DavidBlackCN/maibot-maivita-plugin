-- Offline R2/R3 development. Ports are internal contracts, not host APIs.
CREATE TABLE decision_runs (
    decision_id TEXT PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES life_state(character_id),
    basis_state_version INTEGER NOT NULL,
    basis_plan_version INTEGER NOT NULL,
    basis_config_revision INTEGER NOT NULL,
    started_at_ms INTEGER NOT NULL,
    finished_at_ms INTEGER,
    trigger_ids_json TEXT NOT NULL CHECK(json_valid(trigger_ids_json)),
    status TEXT NOT NULL CHECK(status IN ('running','validated','applied','stale','invalid','uncertain','failed','abandoned')),
    candidate_json TEXT,
    candidate_hash TEXT,
    diagnostic TEXT,
    CHECK(candidate_json IS NULL OR json_valid(candidate_json))
) STRICT;
CREATE UNIQUE INDEX one_running_decision ON decision_runs(character_id) WHERE status='running';
CREATE TABLE integration_settings (
    character_id TEXT PRIMARY KEY REFERENCES life_state(character_id),
    behavior_mode TEXT NOT NULL DEFAULT 'local_compat' CHECK(behavior_mode IN ('local_compat','external')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0)
) STRICT;
CREATE TABLE event_subscriptions (
    consumer_id TEXT PRIMARY KEY,
    event_prefix TEXT NOT NULL CHECK(event_prefix LIKE 'life.%'),
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1))
) STRICT;
