-- R1 schema 1. No legacy goals tables and no R2 decision runner.
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK(version > 0),
    checksum TEXT NOT NULL CHECK(length(checksum) = 64),
    applied_at_ms INTEGER NOT NULL
) STRICT;

CREATE TABLE life_state (
    character_id TEXT PRIMARY KEY CHECK(length(trim(character_id)) > 0),
    state_version INTEGER NOT NULL CHECK(state_version >= 0),
    plan_version INTEGER NOT NULL CHECK(plan_version >= 0),
    config_revision INTEGER NOT NULL CHECK(config_revision >= 0),
    sleep_phase TEXT NOT NULL CHECK(sleep_phase IN ('awake','winding_down','sleeping','temporarily_awake','waking')),
    primary_activity_id TEXT,
    secondary_activity_ids_json TEXT NOT NULL CHECK(json_valid(secondary_activity_ids_json) AND json_type(secondary_activity_ids_json) = 'array' AND json_array_length(secondary_activity_ids_json) = 0),
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

CREATE TABLE plans (
    plan_id TEXT PRIMARY KEY CHECK(length(trim(plan_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    source TEXT NOT NULL,
    level TEXT NOT NULL CHECK(level IN ('direction','daily')),
    version INTEGER NOT NULL CHECK(version > 0),
    status TEXT NOT NULL CHECK(status IN ('draft','active','superseded','archived')),
    local_date TEXT CHECK(local_date IS NULL OR (date(local_date) IS NOT NULL AND date(local_date) = local_date)),
    start_at_ms INTEGER,
    end_at_ms INTEGER,
    CHECK((start_at_ms IS NULL AND end_at_ms IS NULL) OR (start_at_ms IS NOT NULL AND end_at_ms IS NOT NULL AND end_at_ms > start_at_ms)),
    CHECK(local_date IS NOT NULL OR start_at_ms IS NOT NULL),
    UNIQUE(character_id, plan_id)
) STRICT;

CREATE TABLE commitments (
    commitment_id TEXT PRIMARY KEY CHECK(length(trim(commitment_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    title TEXT NOT NULL,
    source_stream_id TEXT NOT NULL,
    provenance TEXT NOT NULL,
    timezone TEXT NOT NULL,
    local_date TEXT CHECK(local_date IS NULL OR (date(local_date) IS NOT NULL AND date(local_date) = local_date)),
    date_end TEXT CHECK(date_end IS NULL OR (local_date IS NOT NULL AND date(date_end) IS NOT NULL AND date(date_end) = date_end AND date_end >= local_date)),
    start_at_ms INTEGER,
    end_at_ms INTEGER,
    status TEXT NOT NULL CHECK(status IN ('proposed','confirmed','fulfilled','cancelled','missed','needs_review')),
    version INTEGER NOT NULL CHECK(version > 0),
    visibility TEXT NOT NULL CHECK(visibility IN ('public_summary','source_stream','restricted')),
    participants_json TEXT NOT NULL CHECK(json_valid(participants_json) AND json_type(participants_json) = 'array'),
    confirmation_basis TEXT,
    fulfillment_evidence TEXT,
    notes TEXT NOT NULL,
    CHECK((start_at_ms IS NULL AND end_at_ms IS NULL) OR (start_at_ms IS NOT NULL AND end_at_ms IS NOT NULL AND end_at_ms > start_at_ms)),
    CHECK(local_date IS NOT NULL OR start_at_ms IS NOT NULL),
    CHECK(status != 'confirmed' OR length(trim(coalesce(confirmation_basis,''))) > 0),
    CHECK(status != 'fulfilled' OR length(trim(coalesce(fulfillment_evidence,''))) > 0),
    UNIQUE(character_id, commitment_id)
) STRICT;

CREATE TABLE schedule_items (
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
    preferred_duration_minutes REAL CHECK(preferred_duration_minutes > 0),
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

CREATE TABLE sleep_sessions (
    session_id TEXT PRIMARY KEY CHECK(length(trim(session_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    started_at_ms INTEGER NOT NULL,
    expected_wake_at_ms INTEGER NOT NULL CHECK(expected_wake_at_ms > started_at_ms),
    hard_deadline_at_ms INTEGER NOT NULL CHECK(hard_deadline_at_ms >= expected_wake_at_ms),
    initial_expected_duration_minutes REAL NOT NULL CHECK(initial_expected_duration_minutes > 0),
    provenance TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('night_sleep','nap')),
    status TEXT NOT NULL CHECK(status IN ('active','interrupted','closed')),
    ended_at_ms INTEGER CHECK(ended_at_ms > started_at_ms AND ended_at_ms <= hard_deadline_at_ms),
    end_reason TEXT,
    decision_id TEXT,
    extension_count INTEGER NOT NULL CHECK(extension_count >= 0),
    interrupt_until_ms INTEGER,
    actual_sleep_minutes REAL NOT NULL CHECK(actual_sleep_minutes >= 0),
    recovery_marker TEXT,
    revision INTEGER NOT NULL CHECK(revision > 0),
    reason_summary TEXT NOT NULL,
    CHECK((status = 'closed') = (ended_at_ms IS NOT NULL)),
    CHECK(status != 'closed' OR length(trim(coalesce(end_reason,''))) > 0),
    CHECK((status = 'interrupted' AND interrupt_until_ms IS NOT NULL AND interrupt_until_ms > started_at_ms AND interrupt_until_ms <= hard_deadline_at_ms)
       OR (status != 'interrupted' AND interrupt_until_ms IS NULL)),
    UNIQUE(character_id, session_id)
) STRICT;
CREATE UNIQUE INDEX one_open_sleep_session ON sleep_sessions(character_id) WHERE status IN ('active','interrupted');

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

CREATE TABLE activities (
    activity_id TEXT PRIMARY KEY CHECK(length(trim(activity_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    name TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    provenance TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('rest','study','work','entertainment','daily_life','social','travel','creative','other')),
    role TEXT NOT NULL CHECK(role = 'primary'),
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
CREATE UNIQUE INDEX one_active_primary ON activities(character_id) WHERE role = 'primary' AND ended_at_ms IS NULL;

CREATE TRIGGER activity_no_overlap_insert BEFORE INSERT ON activities
WHEN EXISTS (SELECT 1 FROM activities WHERE character_id = NEW.character_id
 AND NEW.started_at_ms < coalesce(ended_at_ms, 9223372036854775807)
 AND started_at_ms < coalesce(NEW.ended_at_ms, 9223372036854775807))
BEGIN
    SELECT RAISE(ABORT, 'actual primary activities overlap');
END;

CREATE TRIGGER activity_no_overlap_update BEFORE UPDATE ON activities
WHEN EXISTS (SELECT 1 FROM activities WHERE character_id = NEW.character_id AND activity_id != OLD.activity_id
 AND NEW.started_at_ms < coalesce(ended_at_ms, 9223372036854775807)
 AND started_at_ms < coalesce(NEW.ended_at_ms, 9223372036854775807))
BEGIN
    SELECT RAISE(ABORT, 'actual primary activities overlap');
END;

CREATE TABLE sleep_segments (
    segment_id TEXT PRIMARY KEY CHECK(length(trim(segment_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    session_id TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    provenance TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('sleep','awake_interrupt')),
    ended_at_ms INTEGER CHECK(ended_at_ms > started_at_ms),
    FOREIGN KEY(character_id, session_id) REFERENCES sleep_sessions(character_id, session_id) DEFERRABLE INITIALLY DEFERRED
) STRICT;
CREATE UNIQUE INDEX one_open_sleep_segment ON sleep_segments(session_id) WHERE ended_at_ms IS NULL;

CREATE TABLE life_events (
    event_id TEXT PRIMARY KEY CHECK(length(trim(event_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    event_type TEXT NOT NULL CHECK(event_type LIKE 'life.%'),
    state_version INTEGER NOT NULL CHECK(state_version >= 0),
    event_index INTEGER NOT NULL CHECK(event_index >= 0),
    occurred_at_ms INTEGER NOT NULL,
    recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms >= occurred_at_ms),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json) = 'object'),
    source TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    correlation_id TEXT,
    causation_id TEXT,
    visibility TEXT NOT NULL CHECK(visibility IN ('public_summary','source_stream','restricted')),
    expires_at_ms INTEGER CHECK(expires_at_ms >= occurred_at_ms),
    dispatch_status TEXT NOT NULL DEFAULT 'pending' CHECK(dispatch_status IN ('pending','delivered','failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    next_attempt_at_ms INTEGER,
    UNIQUE(character_id, state_version, event_index)
) STRICT;

CREATE TABLE schedule_revisions (
    revision_id TEXT PRIMARY KEY CHECK(length(trim(revision_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    plan_id TEXT NOT NULL,
    basis_version INTEGER NOT NULL CHECK(basis_version >= 0),
    result_version INTEGER NOT NULL CHECK(result_version = basis_version + 1),
    occurred_at_ms INTEGER NOT NULL,
    reason_summary TEXT NOT NULL,
    changes_json TEXT NOT NULL CHECK(json_valid(changes_json) AND json_type(changes_json) = 'array'),
    decision_id TEXT,
    trigger_event_id TEXT,
    actor TEXT NOT NULL,
    UNIQUE(plan_id, result_version),
    FOREIGN KEY(character_id, plan_id) REFERENCES plans(character_id, plan_id) DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TABLE life_goals (
    goal_id TEXT PRIMARY KEY CHECK(length(trim(goal_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    title TEXT NOT NULL,
    provenance TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','paused','completed','cancelled')),
    stage TEXT NOT NULL,
    deadline_at_ms INTEGER,
    progress_evidence_json TEXT NOT NULL CHECK(json_valid(progress_evidence_json) AND json_type(progress_evidence_json) = 'array'),
    visibility TEXT NOT NULL CHECK(visibility IN ('public_summary','source_stream','restricted'))
) STRICT;

CREATE TABLE life_preferences (
    preference_id TEXT PRIMARY KEY CHECK(length(trim(preference_id)) > 0),
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('reference','hard_constraint')),
    allow_evolution INTEGER NOT NULL CHECK(allow_evolution IN (0,1)),
    version INTEGER NOT NULL CHECK(version > 0)
) STRICT;

-- R1-03/04 storage contracts only; command processing/import remain separate tasks.
CREATE TABLE command_receipts (
    character_id TEXT NOT NULL REFERENCES life_state(character_id) DEFERRABLE INITIALLY DEFERRED,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    result_json TEXT NOT NULL CHECK(json_valid(result_json)),
    state_version INTEGER NOT NULL CHECK(state_version >= 0),
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY(character_id, idempotency_key)
) STRICT;

CREATE TABLE event_deliveries (
    event_id TEXT NOT NULL REFERENCES life_events(event_id),
    consumer_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','acknowledged','expired','failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    next_attempt_at_ms INTEGER,
    acknowledged_at_ms INTEGER,
    PRIMARY KEY(event_id, consumer_id)
) STRICT;

CREATE TABLE migration_runs (
    migration_run_id TEXT PRIMARY KEY,
    source_database_id TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('discovered','backed_up','staged','validated','activated','failed','rolled_back')),
    started_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER CHECK(completed_at_ms >= started_at_ms),
    report_json TEXT NOT NULL CHECK(json_valid(report_json))
) STRICT;

CREATE TABLE legacy_import_records (
    import_record_id TEXT PRIMARY KEY,
    migration_run_id TEXT NOT NULL REFERENCES migration_runs(migration_run_id),
    source_database_id TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_primary_key TEXT NOT NULL,
    source_row_hash TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification IN ('commitment','schedule','goal','archive','needs_review')),
    review_reason TEXT,
    UNIQUE(source_database_id, source_table, source_primary_key)
) STRICT;

CREATE TABLE legacy_id_map (
    import_record_id TEXT NOT NULL REFERENCES legacy_import_records(import_record_id),
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    PRIMARY KEY(import_record_id, target_type, target_id)
) STRICT;

CREATE TRIGGER life_state_versions BEFORE UPDATE ON life_state
WHEN NEW.character_id != OLD.character_id OR NEW.state_version <= OLD.state_version
 OR NEW.plan_version < OLD.plan_version OR NEW.config_revision < OLD.config_revision
 OR NEW.updated_at_ms < OLD.updated_at_ms OR NEW.last_advanced_at_ms < OLD.last_advanced_at_ms
BEGIN
    SELECT RAISE(ABORT, 'state identity/version/time regression');
END;

CREATE TRIGGER plan_versions BEFORE UPDATE ON plans
WHEN NEW.plan_id != OLD.plan_id OR NEW.character_id != OLD.character_id OR NEW.version <= OLD.version
BEGIN
    SELECT RAISE(ABORT, 'plan identity/version regression');
END;

CREATE TRIGGER commitment_versions BEFORE UPDATE ON commitments
WHEN NEW.commitment_id != OLD.commitment_id OR NEW.character_id != OLD.character_id OR NEW.version <= OLD.version
BEGIN
    SELECT RAISE(ABORT, 'commitment identity/version regression');
END;

CREATE TRIGGER preference_versions BEFORE UPDATE ON life_preferences
WHEN NEW.preference_id != OLD.preference_id OR NEW.character_id != OLD.character_id OR NEW.version <= OLD.version
BEGIN
    SELECT RAISE(ABORT, 'preference identity/version regression');
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

CREATE TRIGGER segment_facts_immutable BEFORE UPDATE ON sleep_segments
WHEN OLD.ended_at_ms IS NOT NULL OR NEW.ended_at_ms IS NULL
 OR NEW.segment_id != OLD.segment_id OR NEW.character_id != OLD.character_id OR NEW.session_id != OLD.session_id
 OR NEW.started_at_ms != OLD.started_at_ms OR NEW.kind != OLD.kind OR NEW.provenance != OLD.provenance
BEGIN
    SELECT RAISE(ABORT, 'segment facts are immutable');
END;

CREATE TRIGGER segment_no_delete BEFORE DELETE ON sleep_segments
BEGIN
    SELECT RAISE(ABORT, 'sleep segment history cannot be deleted');
END;

CREATE TRIGGER segment_no_overlap_insert BEFORE INSERT ON sleep_segments
WHEN EXISTS (SELECT 1 FROM sleep_segments WHERE session_id = NEW.session_id
 AND NEW.started_at_ms < coalesce(ended_at_ms, 9223372036854775807)
 AND started_at_ms < coalesce(NEW.ended_at_ms, 9223372036854775807))
BEGIN
    SELECT RAISE(ABORT, 'sleep segments overlap');
END;

CREATE TRIGGER segment_no_overlap_update BEFORE UPDATE ON sleep_segments
WHEN EXISTS (SELECT 1 FROM sleep_segments WHERE session_id = NEW.session_id AND segment_id != OLD.segment_id
 AND NEW.started_at_ms < coalesce(ended_at_ms, 9223372036854775807)
 AND started_at_ms < coalesce(NEW.ended_at_ms, 9223372036854775807))
BEGIN
    SELECT RAISE(ABORT, 'sleep segments overlap');
END;

CREATE TRIGGER event_facts_immutable BEFORE UPDATE ON life_events
WHEN NEW.event_id != OLD.event_id OR NEW.character_id != OLD.character_id OR NEW.event_type != OLD.event_type
 OR NEW.state_version != OLD.state_version OR NEW.event_index != OLD.event_index
 OR NEW.occurred_at_ms != OLD.occurred_at_ms OR NEW.recorded_at_ms != OLD.recorded_at_ms
 OR NEW.payload_json != OLD.payload_json OR NEW.source != OLD.source OR NEW.schema_version != OLD.schema_version
 OR NEW.correlation_id IS NOT OLD.correlation_id OR NEW.causation_id IS NOT OLD.causation_id
 OR NEW.visibility != OLD.visibility OR NEW.expires_at_ms IS NOT OLD.expires_at_ms
BEGIN
    SELECT RAISE(ABORT, 'event facts are immutable');
END;

CREATE TRIGGER event_no_delete BEFORE DELETE ON life_events
BEGIN
    SELECT RAISE(ABORT, 'event history cannot be deleted');
END;

CREATE TRIGGER revision_no_update BEFORE UPDATE ON schedule_revisions
BEGIN
    SELECT RAISE(ABORT, 'plan revisions are immutable');
END;

CREATE TRIGGER revision_no_delete BEFORE DELETE ON schedule_revisions
BEGIN
    SELECT RAISE(ABORT, 'plan revisions cannot be deleted');
END;
