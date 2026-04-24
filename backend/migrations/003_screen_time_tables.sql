-- Screen Time Control tables
-- Run this in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS screen_time_rules (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    required_sessions INT NOT NULL DEFAULT 3,
    unlock_minutes INT NOT NULL DEFAULT 30,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(student_id)
);

CREATE INDEX IF NOT EXISTS idx_screen_time_rules_student
    ON screen_time_rules(student_id);

CREATE TABLE IF NOT EXISTS screen_time_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,       -- 'unlock', 'relock', 'poll', 'authorize'
    details JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_screen_time_events_student
    ON screen_time_events(student_id);

CREATE INDEX IF NOT EXISTS idx_screen_time_events_type
    ON screen_time_events(event_type);
