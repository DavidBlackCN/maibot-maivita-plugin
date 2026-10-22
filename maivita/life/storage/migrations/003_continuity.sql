-- R4 explicit table rebuild; migration runner disables FK enforcement only
-- outside the exclusive transaction, then validates every FK before commit.


CREATE TABLE life_state_next (
    character_id TEXT PRIMARY KEY CHECK(length(trim(character_id)) > 0),
    state_version INTEGER NOT NULL CHECK(state_version >= 0),
    plan_version INTEGER NOT NULL CHECK(plan_version >= 0),
    config_revision INTEGER NOT NULL CHECK(config_revision >= 0),
    sleep_phase TEXT NOT NULL CHECK(sleep_phase IN ('awake','winding_down','sleeping','temporarily_awake','waking')),
    primary_activity_id TEXT,
    secondary_activity_ids_json TEXT NOT NULL CHECK(json_valid(secondary_activity_ids_json) AND json_type(secondary_activity_ids_json) = 'array' AND json_array_length(secondary_activity_ids_json) <= 2),
    active_sleep_session_id TEXT,
    active_plan_id TEXT,
    last_wake_at_ms INTEGER,
    awake_since_at_ms INTEGER,
    energy REAL CHECK(energy BETWEEN 0 AND 100),
    fatigue REAL CHECK(fatigue BETWEEN 0 AND 100),
    stress REAL CHECK(stress BETWEEN 0 AND 100),
    sleep_debt_minutes REAL CHECK(sleep_debt_minutes >= 0),
    sleep_history_coverage_days INTEGER NOT NULL CHECK(sleep_history_coverage_days BETWEEN 0 AND 7),
    updated_at_ms INTEGER NOT NULL,
    last_advanced_at_ms INTEGER NOT NULL CHECK(last_advanced_at_ms <= updated_at_ms),
    provenance TEXT NOT NULL CHECK(length(trim(provenance)) > 0),
    confidence TEXT NOT NULL,
    degraded_reason TEXT,
    CHECK(last_wake_at_ms IS NULL OR last_wake_at_ms <= updated_at_ms),
    CHECK(awake_since_at_ms IS NULL OR awake_since_at_ms <= updated_at_ms),
    CHECK(sleep_debt_minutes IS NULL OR sleep_history_coverage_days > 0),
    CHECK((sleep_phase IN ('sleeping','temporarily_awake')) = (active_sleep_session_id IS NOT NULL)),
    CHECK(sleep_phase != 'sleeping' OR awake_since_at_ms IS NULL),
    CHECK(sleep_phase != 'temporarily_awake' OR awake_since_at_ms IS NOT NULL),
    FOREIGN KEY(character_id, primary_activity_id) REFERENCES activities(character_id, activity_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(character_id, active_sleep_session_id) REFERENCES sleep_sessions(character_id, session_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(character_id, active_plan_id) REFERENCES plans(character_id, plan_id) DEFERRABLE INITIALLY DEFERRED
) STRICT;

INSERT INTO life_state_next SELECT * FROM life_state;

DROP TABLE life_state;

ALTER TABLE life_state_next RENAME TO life_state;

CREATE TRIGGER life_state_versions BEFORE UPDATE ON life_state
WHEN NEW.character_id != OLD.character_id OR NEW.state_version <= OLD.state_version
 OR NEW.plan_version < OLD.plan_version OR NEW.config_revision < OLD.config_revision
 OR NEW.updated_at_ms < OLD.updated_at_ms OR NEW.last_advanced_at_ms < OLD.last_advanced_at_ms
BEGIN
    SELECT RAISE(ABORT, 'state identity/version/time regression');
END;

CREATE TABLE activities_next (
    activity_id TEXT PRIMARY KEY CHECK(length(trim(activity_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    name TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    provenance TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('rest','study','work','entertainment','daily_life','social','travel','creative','other')),
    role TEXT NOT NULL CHECK(role IN ('primary','secondary')),
    description TEXT NOT NULL,
    ended_at_ms INTEGER CHECK(ended_at_ms > started_at_ms),
    status TEXT NOT NULL CHECK(status IN ('active','completed','interrupted','abandoned')),
    source_schedule_item_id TEXT,
    resumes_activity_id TEXT CHECK(resumes_activity_id != activity_id),
    sleep_session_id TEXT,
    interruption_reason TEXT,
    end_reason TEXT,
    visibility TEXT NOT NULL CHECK(visibility IN ('public_summary','source_stream','restricted')),
    CHECK((status = 'active') = (ended_at_ms IS NULL)),
    CHECK(status = 'active' OR length(trim(coalesce(end_reason,''))) > 0),
    UNIQUE(character_id, activity_id),
    FOREIGN KEY(character_id, source_schedule_item_id) REFERENCES schedule_items(character_id, item_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(character_id, resumes_activity_id) REFERENCES activities(character_id, activity_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(character_id, sleep_session_id) REFERENCES sleep_sessions(character_id, session_id) DEFERRABLE INITIALLY DEFERRED
) STRICT;

INSERT INTO activities_next SELECT * FROM activities;

DROP TABLE activities;

ALTER TABLE activities_next RENAME TO activities;

CREATE UNIQUE INDEX one_active_primary ON activities(character_id) WHERE role = 'primary' AND ended_at_ms IS NULL;

CREATE TRIGGER activity_no_overlap_insert BEFORE INSERT ON activities
WHEN NEW.role = 'primary' AND EXISTS (SELECT 1 FROM activities WHERE role = 'primary' AND character_id = NEW.character_id
 AND NEW.started_at_ms < coalesce(ended_at_ms, 9223372036854775807)
 AND started_at_ms < coalesce(NEW.ended_at_ms, 9223372036854775807))
BEGIN
    SELECT RAISE(ABORT, 'actual primary activities overlap');
END;

CREATE TRIGGER activity_no_overlap_update BEFORE UPDATE ON activities
WHEN NEW.role = 'primary' AND EXISTS (SELECT 1 FROM activities WHERE role = 'primary' AND character_id = NEW.character_id AND activity_id != OLD.activity_id
 AND NEW.started_at_ms < coalesce(ended_at_ms, 9223372036854775807)
 AND started_at_ms < coalesce(NEW.ended_at_ms, 9223372036854775807))
BEGIN
    SELECT RAISE(ABORT, 'actual primary activities overlap');
END;

CREATE TRIGGER activity_facts_immutable BEFORE UPDATE ON activities
WHEN OLD.ended_at_ms IS NOT NULL OR NEW.status = 'active'
 OR NEW.activity_id != OLD.activity_id OR NEW.character_id != OLD.character_id
 OR NEW.started_at_ms != OLD.started_at_ms OR NEW.name != OLD.name OR NEW.description != OLD.description
 OR NEW.role != OLD.role OR NEW.category != OLD.category OR NEW.provenance != OLD.provenance
 OR NEW.source_schedule_item_id IS NOT OLD.source_schedule_item_id
 OR NEW.resumes_activity_id IS NOT OLD.resumes_activity_id OR NEW.sleep_session_id IS NOT OLD.sleep_session_id
 OR NEW.visibility != OLD.visibility
BEGIN
    SELECT RAISE(ABORT, 'activity facts are immutable');
END;

CREATE TRIGGER activity_no_delete BEFORE DELETE ON activities
BEGIN
    SELECT RAISE(ABORT, 'activity history cannot be deleted');
END;

CREATE TRIGGER secondary_limit_insert BEFORE INSERT ON activities
WHEN NEW.role='secondary' AND NEW.status='active' AND
 ((SELECT count(*) FROM activities WHERE character_id=NEW.character_id AND role='secondary' AND status='active') >= 2
  OR EXISTS(SELECT 1 FROM sleep_sessions WHERE character_id=NEW.character_id AND status!='closed'))
BEGIN
 SELECT RAISE(ABORT,'secondary activity limit or sleeping');
END;
CREATE TABLE habits (
 habit_id TEXT PRIMARY KEY, character_id TEXT NOT NULL REFERENCES life_state(character_id),
 content TEXT NOT NULL, evidence_ids_json TEXT NOT NULL CHECK(json_valid(evidence_ids_json)),
 status TEXT NOT NULL CHECK(status IN ('candidate','active','archived')),
 confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1), weight REAL NOT NULL CHECK(weight>=0),
 last_seen_at_ms INTEGER, last_reviewed_at_ms INTEGER
) STRICT;
CREATE TABLE life_summaries (
 summary_id TEXT PRIMARY KEY, character_id TEXT NOT NULL REFERENCES life_state(character_id),
 start_at_ms INTEGER NOT NULL, end_at_ms INTEGER NOT NULL CHECK(end_at_ms>start_at_ms),
 content TEXT NOT NULL, source_checksum TEXT NOT NULL,
 source_ids_json TEXT NOT NULL CHECK(json_valid(source_ids_json)),
 visibility TEXT NOT NULL CHECK(visibility IN ('public_summary','source_stream','restricted'))
) STRICT;
CREATE TABLE retention_audit (
 cleanup_id TEXT PRIMARY KEY, summary_id TEXT NOT NULL REFERENCES life_summaries(summary_id),
 occurred_at_ms INTEGER NOT NULL, removed_count INTEGER NOT NULL CHECK(removed_count>=0),
 source_checksum TEXT NOT NULL
) STRICT;
CREATE TABLE memory_deliveries (
 summary_id TEXT NOT NULL REFERENCES life_summaries(summary_id), consumer_id TEXT NOT NULL,
 acknowledged_at_ms INTEGER NOT NULL, PRIMARY KEY(summary_id,consumer_id)
) STRICT;
