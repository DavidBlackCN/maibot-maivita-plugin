-- SQLite 3.37.2 cannot reliably UPDATE REAL columns in STRICT tables with
-- BEFORE UPDATE triggers. Preserve STRICT, all constraints and all triggers.
-- The migration runner disables FKs before its exclusive transaction and
-- checks integrity/FKs before commit; any failure rolls back the entire rebuild.
-- Percentages: hundredths; energy_rate: thousandths; durations: whole minutes.
-- Round in domain units before scaling to avoid binary multiplication at ties
-- (for example 1.005 * 100). ROUND uses ties away from zero. Positive durations have a one-minute minimum.
-- NULL remains unknown. habits.confidence/weight have no triggers and stay REAL.


DROP TRIGGER activity_context_immutable;

DROP TRIGGER activity_facts_immutable;

DROP TRIGGER activity_no_delete;

DROP TRIGGER activity_no_overlap_insert;

DROP TRIGGER activity_no_overlap_update;

DROP TRIGGER life_state_versions;

DROP TRIGGER secondary_limit_insert;

DROP TRIGGER session_facts_immutable;

DROP TRIGGER session_no_delete;

DROP TRIGGER session_no_overlap_insert;

DROP TRIGGER session_no_overlap_update;

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
    energy INTEGER CHECK(energy BETWEEN 0 AND 10000),
    fatigue INTEGER CHECK(fatigue BETWEEN 0 AND 10000),
    stress INTEGER CHECK(stress BETWEEN 0 AND 10000),
    sleep_debt_minutes INTEGER CHECK(sleep_debt_minutes >= 0),
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

INSERT INTO life_state_next (
    character_id, state_version, plan_version, config_revision, sleep_phase, primary_activity_id, secondary_activity_ids_json, active_sleep_session_id, active_plan_id, last_wake_at_ms, awake_since_at_ms, energy, fatigue, stress, sleep_debt_minutes, sleep_history_coverage_days, updated_at_ms, last_advanced_at_ms, provenance, confidence, degraded_reason
) SELECT
    character_id,
    state_version,
    plan_version,
    config_revision,
    sleep_phase,
    primary_activity_id,
    secondary_activity_ids_json,
    active_sleep_session_id,
    active_plan_id,
    last_wake_at_ms,
    awake_since_at_ms,
    CAST(ROUND(ROUND(energy, 2) * 100) AS INTEGER),
    CAST(ROUND(ROUND(fatigue, 2) * 100) AS INTEGER),
    CAST(ROUND(ROUND(stress, 2) * 100) AS INTEGER),
    CAST(ROUND(sleep_debt_minutes) AS INTEGER),
    sleep_history_coverage_days,
    updated_at_ms,
    last_advanced_at_ms,
    provenance,
    confidence,
    degraded_reason
FROM life_state;

DROP TABLE life_state;

ALTER TABLE life_state_next RENAME TO life_state;

CREATE TABLE schedule_items_next (
    item_id TEXT PRIMARY KEY CHECK(length(trim(item_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    plan_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    time_mode TEXT NOT NULL CHECK(time_mode IN ('fixed','window','unscheduled')),
    category TEXT NOT NULL CHECK(category IN ('rest','study','work','entertainment','daily_life','social','travel','creative','other')),
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('planned','started','done','cancelled','superseded')),
    start_at_ms INTEGER,
    end_at_ms INTEGER,
    window_start_at_ms INTEGER,
    window_end_at_ms INTEGER,
    preferred_start_at_ms INTEGER,
    preferred_duration_minutes INTEGER CHECK(preferred_duration_minutes > 0),
    priority TEXT NOT NULL CHECK(priority IN ('low','medium','high')),
    commitment_id TEXT,
    visibility TEXT NOT NULL CHECK(visibility IN ('public_summary','source_stream','restricted')),
    completion_evidence TEXT,
    CHECK(
      (time_mode = 'fixed' AND start_at_ms IS NOT NULL AND end_at_ms IS NOT NULL AND end_at_ms > start_at_ms
       AND window_start_at_ms IS NULL AND window_end_at_ms IS NULL AND preferred_start_at_ms IS NULL AND preferred_duration_minutes IS NULL)
      OR (time_mode = 'window' AND start_at_ms IS NULL AND end_at_ms IS NULL
       AND window_start_at_ms IS NOT NULL AND window_end_at_ms IS NOT NULL AND window_end_at_ms > window_start_at_ms
       AND preferred_duration_minutes IS NOT NULL AND preferred_duration_minutes * 60000 <= window_end_at_ms - window_start_at_ms
       AND (preferred_start_at_ms IS NULL OR (preferred_start_at_ms >= window_start_at_ms
        AND preferred_start_at_ms < window_end_at_ms AND preferred_duration_minutes * 60000 <= window_end_at_ms - preferred_start_at_ms)))
      OR (time_mode = 'unscheduled' AND start_at_ms IS NULL AND end_at_ms IS NULL
       AND window_start_at_ms IS NULL AND window_end_at_ms IS NULL AND preferred_start_at_ms IS NULL)
    ),
    CHECK(status != 'done' OR length(trim(coalesce(completion_evidence,''))) > 0),
    UNIQUE(character_id, item_id),
    FOREIGN KEY(character_id, plan_id) REFERENCES plans(character_id, plan_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(character_id, commitment_id) REFERENCES commitments(character_id, commitment_id) DEFERRABLE INITIALLY DEFERRED
) STRICT;

INSERT INTO schedule_items_next (
    item_id, character_id, plan_id, name, source, time_mode, category, kind, status, start_at_ms, end_at_ms, window_start_at_ms, window_end_at_ms, preferred_start_at_ms, preferred_duration_minutes, priority, commitment_id, visibility, completion_evidence
) SELECT
    item_id,
    character_id,
    plan_id,
    name,
    source,
    time_mode,
    category,
    kind,
    status,
    start_at_ms,
    end_at_ms,
    window_start_at_ms,
    window_end_at_ms,
    preferred_start_at_ms,
    CASE WHEN preferred_duration_minutes IS NULL THEN NULL ELSE MAX(1, CAST(ROUND(preferred_duration_minutes) AS INTEGER)) END,
    priority,
    commitment_id,
    visibility,
    completion_evidence
FROM schedule_items;

DROP TABLE schedule_items;

ALTER TABLE schedule_items_next RENAME TO schedule_items;

CREATE TABLE sleep_sessions_next (
    session_id TEXT PRIMARY KEY CHECK(length(trim(session_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    started_at_ms INTEGER NOT NULL,
    expected_wake_at_ms INTEGER NOT NULL CHECK(expected_wake_at_ms > started_at_ms),
    hard_deadline_at_ms INTEGER NOT NULL CHECK(hard_deadline_at_ms >= expected_wake_at_ms),
    initial_expected_duration_minutes INTEGER NOT NULL CHECK(initial_expected_duration_minutes > 0),
    provenance TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('night_sleep','nap')),
    status TEXT NOT NULL CHECK(status IN ('active','interrupted','closed')),
    ended_at_ms INTEGER CHECK(ended_at_ms > started_at_ms AND ended_at_ms <= hard_deadline_at_ms),
    end_reason TEXT,
    decision_id TEXT,
    extension_count INTEGER NOT NULL CHECK(extension_count >= 0),
    interrupt_until_ms INTEGER,
    actual_sleep_minutes INTEGER NOT NULL CHECK(actual_sleep_minutes >= 0),
    recovery_marker TEXT,
    revision INTEGER NOT NULL CHECK(revision > 0),
    reason_summary TEXT NOT NULL,
    CHECK((status = 'closed') = (ended_at_ms IS NOT NULL)),
    CHECK(status != 'closed' OR length(trim(coalesce(end_reason,''))) > 0),
    CHECK((status = 'interrupted' AND interrupt_until_ms IS NOT NULL AND interrupt_until_ms > started_at_ms AND interrupt_until_ms <= hard_deadline_at_ms)
       OR (status != 'interrupted' AND interrupt_until_ms IS NULL)),
    UNIQUE(character_id, session_id)
) STRICT;

INSERT INTO sleep_sessions_next (
    session_id, character_id, started_at_ms, expected_wake_at_ms, hard_deadline_at_ms, initial_expected_duration_minutes, provenance, kind, status, ended_at_ms, end_reason, decision_id, extension_count, interrupt_until_ms, actual_sleep_minutes, recovery_marker, revision, reason_summary
) SELECT
    session_id,
    character_id,
    started_at_ms,
    expected_wake_at_ms,
    hard_deadline_at_ms,
    CASE WHEN initial_expected_duration_minutes IS NULL THEN NULL ELSE MAX(1, CAST(ROUND(initial_expected_duration_minutes) AS INTEGER)) END,
    provenance,
    kind,
    status,
    ended_at_ms,
    end_reason,
    decision_id,
    extension_count,
    interrupt_until_ms,
    CAST(ROUND(actual_sleep_minutes) AS INTEGER),
    recovery_marker,
    revision,
    reason_summary
FROM sleep_sessions;

DROP TABLE sleep_sessions;

ALTER TABLE sleep_sessions_next RENAME TO sleep_sessions;

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
    location TEXT NOT NULL DEFAULT '',
    emotion TEXT NOT NULL DEFAULT '',
    energy_rate INTEGER NOT NULL DEFAULT 0,
    CHECK((status = 'active') = (ended_at_ms IS NULL)),
    CHECK(status = 'active' OR length(trim(coalesce(end_reason,''))) > 0),
    UNIQUE(character_id, activity_id),
    FOREIGN KEY(character_id, source_schedule_item_id) REFERENCES schedule_items(character_id, item_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(character_id, resumes_activity_id) REFERENCES activities(character_id, activity_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(character_id, sleep_session_id) REFERENCES sleep_sessions(character_id, session_id) DEFERRABLE INITIALLY DEFERRED
) STRICT;

INSERT INTO activities_next (
    activity_id, character_id, name, started_at_ms, provenance, category, role, description, ended_at_ms, status, source_schedule_item_id, resumes_activity_id, sleep_session_id, interruption_reason, end_reason, visibility, location, emotion, energy_rate
) SELECT
    activity_id,
    character_id,
    name,
    started_at_ms,
    provenance,
    category,
    role,
    description,
    ended_at_ms,
    status,
    source_schedule_item_id,
    resumes_activity_id,
    sleep_session_id,
    interruption_reason,
    end_reason,
    visibility,
    location,
    emotion,
    CAST(ROUND(ROUND(energy_rate, 3) * 1000) AS INTEGER)
FROM activities;

DROP TABLE activities;

ALTER TABLE activities_next RENAME TO activities;

CREATE UNIQUE INDEX one_active_primary ON activities(character_id) WHERE role = 'primary' AND ended_at_ms IS NULL;

CREATE UNIQUE INDEX one_open_sleep_session ON sleep_sessions(character_id) WHERE status IN ('active','interrupted');

CREATE TRIGGER activity_context_immutable BEFORE UPDATE ON activities
WHEN NEW.location != OLD.location OR NEW.emotion != OLD.emotion
 OR NEW.energy_rate != OLD.energy_rate
BEGIN
    SELECT RAISE(ABORT, 'activity context is immutable');
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

CREATE TRIGGER life_state_versions BEFORE UPDATE ON life_state
WHEN NEW.character_id != OLD.character_id OR NEW.state_version <= OLD.state_version
 OR NEW.plan_version < OLD.plan_version OR NEW.config_revision < OLD.config_revision
 OR NEW.updated_at_ms < OLD.updated_at_ms OR NEW.last_advanced_at_ms < OLD.last_advanced_at_ms
BEGIN
    SELECT RAISE(ABORT, 'state identity/version/time regression');
END;

CREATE TRIGGER secondary_limit_insert BEFORE INSERT ON activities
WHEN NEW.role='secondary' AND NEW.status='active' AND
 ((SELECT count(*) FROM activities WHERE character_id=NEW.character_id AND role='secondary' AND status='active') >= 2
  OR EXISTS(SELECT 1 FROM sleep_sessions WHERE character_id=NEW.character_id AND status!='closed'))
BEGIN
 SELECT RAISE(ABORT,'secondary activity limit or sleeping');
END;

CREATE TRIGGER session_facts_immutable BEFORE UPDATE ON sleep_sessions
WHEN OLD.status = 'closed' OR NEW.session_id != OLD.session_id OR NEW.character_id != OLD.character_id
 OR NEW.started_at_ms != OLD.started_at_ms OR NEW.provenance != OLD.provenance OR NEW.kind != OLD.kind
 OR NEW.initial_expected_duration_minutes != OLD.initial_expected_duration_minutes OR NEW.revision <= OLD.revision
 OR NEW.decision_id IS NOT OLD.decision_id
BEGIN
    SELECT RAISE(ABORT, 'session facts/revision are immutable');
END;

CREATE TRIGGER session_no_delete BEFORE DELETE ON sleep_sessions
BEGIN
    SELECT RAISE(ABORT, 'sleep history cannot be deleted');
END;

CREATE TRIGGER session_no_overlap_insert BEFORE INSERT ON sleep_sessions
WHEN EXISTS (SELECT 1 FROM sleep_sessions WHERE character_id = NEW.character_id
 AND NEW.started_at_ms < coalesce(ended_at_ms, 9223372036854775807)
 AND started_at_ms < coalesce(NEW.ended_at_ms, 9223372036854775807))
BEGIN
    SELECT RAISE(ABORT, 'sleep session history overlaps');
END;

CREATE TRIGGER session_no_overlap_update BEFORE UPDATE ON sleep_sessions
WHEN EXISTS (SELECT 1 FROM sleep_sessions WHERE character_id = NEW.character_id AND session_id != OLD.session_id
 AND NEW.started_at_ms < coalesce(ended_at_ms, 9223372036854775807)
 AND started_at_ms < coalesce(NEW.ended_at_ms, 9223372036854775807))
BEGIN
    SELECT RAISE(ABORT, 'sleep session history overlaps');
END;
