CREATE TABLE activity_checkpoints (
 activity_id TEXT PRIMARY KEY REFERENCES activities(activity_id),
 deadline_at_ms INTEGER NOT NULL, processed INTEGER NOT NULL DEFAULT 0 CHECK(processed IN (0,1))
) STRICT;
CREATE TABLE decision_triggers (
 trigger_id TEXT PRIMARY KEY, character_id TEXT NOT NULL REFERENCES life_state(character_id),
 kind TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
 consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed IN (0,1))
) STRICT;
CREATE TABLE plan_item_checks (
 item_id TEXT PRIMARY KEY REFERENCES schedule_items(item_id),
 deadline_at_ms INTEGER NOT NULL, plan_version INTEGER NOT NULL,
 processed INTEGER NOT NULL DEFAULT 0 CHECK(processed IN (0,1))
) STRICT;
CREATE TABLE observation_gaps (
 gap_id TEXT PRIMARY KEY, character_id TEXT NOT NULL REFERENCES life_state(character_id),
 start_at_ms INTEGER NOT NULL, end_at_ms INTEGER NOT NULL CHECK(end_at_ms>start_at_ms),
 reason TEXT NOT NULL
) STRICT;
