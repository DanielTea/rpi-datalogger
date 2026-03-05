CREATE TABLE device_logs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_id   TEXT NOT NULL,
    level       TEXT NOT NULL,
    component   TEXT NOT NULL,
    message     TEXT NOT NULL,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_device_logs_device_time ON device_logs (device_id, timestamp);
CREATE INDEX idx_device_logs_level ON device_logs (level);

ALTER TABLE device_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service insert" ON device_logs FOR INSERT WITH CHECK (true);
CREATE POLICY "Service select" ON device_logs FOR SELECT USING (true);
