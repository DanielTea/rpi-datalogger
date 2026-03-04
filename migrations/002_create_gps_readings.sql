CREATE TABLE gps_readings (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_id   TEXT NOT NULL,
    latitude    DOUBLE PRECISION NOT NULL,
    longitude   DOUBLE PRECISION NOT NULL,
    altitude    DOUBLE PRECISION,
    speed       DOUBLE PRECISION,
    course      DOUBLE PRECISION,
    raw_response TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_gps_readings_device_time ON gps_readings (device_id, timestamp);

ALTER TABLE gps_readings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service insert" ON gps_readings FOR INSERT WITH CHECK (true);
CREATE POLICY "Service select" ON gps_readings FOR SELECT USING (true);
