-- One short-lived invitation and one interrupted activity, never a task queue.
CREATE TABLE conversation_state (
    character_id TEXT PRIMARY KEY,
    invitation_id TEXT,
    activity_id TEXT,
    offered_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    paused_activity_id TEXT,
    resume_until_ms INTEGER
);
