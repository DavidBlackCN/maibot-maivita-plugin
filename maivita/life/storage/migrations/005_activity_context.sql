ALTER TABLE activities ADD COLUMN location TEXT NOT NULL DEFAULT '';
ALTER TABLE activities ADD COLUMN emotion TEXT NOT NULL DEFAULT '';
ALTER TABLE activities ADD COLUMN energy_rate REAL NOT NULL DEFAULT 0.0;

-- Context is an observed fact, just like name/category. Keep it immutable
-- even on the one permitted update which closes an active activity.
CREATE TRIGGER activity_context_immutable BEFORE UPDATE ON activities
WHEN NEW.location != OLD.location OR NEW.emotion != OLD.emotion
 OR NEW.energy_rate != OLD.energy_rate
BEGIN
    SELECT RAISE(ABORT, 'activity context is immutable');
END;
