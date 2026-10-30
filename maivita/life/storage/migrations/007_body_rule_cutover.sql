-- New rates apply only after this upgrade; existing estimates/history survive.
CREATE TABLE body_rule_cutovers (
    rule_version TEXT PRIMARY KEY,
    applied_at_ms INTEGER NOT NULL
);
