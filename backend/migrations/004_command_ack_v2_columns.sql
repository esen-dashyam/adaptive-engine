-- Phase 6 Task 6.4 — extend evlin_commands with v2 ack fields
-- Run this in Supabase SQL Editor against the Evlin database.

ALTER TABLE evlin_commands
    ADD COLUMN IF NOT EXISTS ack_verb             VARCHAR(16),
    ADD COLUMN IF NOT EXISTS ack_effective_state  JSONB,
    ADD COLUMN IF NOT EXISTS ack_card_id          VARCHAR(8),
    ADD COLUMN IF NOT EXISTS ack_context          JSONB;

-- Extend the evlin_ack_status enum with the v2 values used by the new executor.
DO $$
BEGIN
    BEGIN
        ALTER TYPE evlin_ack_status ADD VALUE IF NOT EXISTS 'confirmed';
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
    BEGIN
        ALTER TYPE evlin_ack_status ADD VALUE IF NOT EXISTS 'pending_confirmation';
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
END $$;

COMMENT ON COLUMN evlin_commands.ack_verb IS
    'Verb that succeeded on the child (shield/block/unshield/unblock/unshield_all/unblock_all). '
    'Drives parent receipt copy (plan Phase 6 Task 6.4).';
COMMENT ON COLUMN evlin_commands.ack_effective_state IS
    'Coverage snapshot after mutation. JSON shape: '
    '{isBlocked:bool, shieldsCovering:[{displayName,expiresAtISO,tier}], possibleSavedListCoverage:bool}';
COMMENT ON COLUMN evlin_commands.ack_card_id IS
    'Card ID requested by child when status=pending_confirmation (e.g. "B1").';
COMMENT ON COLUMN evlin_commands.ack_context IS
    'Opaque context dict for the pending_confirmation card.';
