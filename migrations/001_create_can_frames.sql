CREATE TABLE can_frames (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_id   TEXT NOT NULL,
    arb_id      INTEGER NOT NULL,
    is_extended BOOLEAN NOT NULL DEFAULT FALSE,
    is_remote   BOOLEAN NOT NULL DEFAULT FALSE,
    dlc         SMALLINT NOT NULL,
    data        BYTEA NOT NULL,
    bus_time    DOUBLE PRECISION,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_can_frames_device_time ON can_frames (device_id, timestamp);
CREATE INDEX idx_can_frames_arb_id ON can_frames (arb_id);

ALTER TABLE can_frames ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service insert" ON can_frames FOR INSERT WITH CHECK (true);
CREATE POLICY "Service select" ON can_frames FOR SELECT USING (true);
