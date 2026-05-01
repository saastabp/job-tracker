-- Slice 07: scheduling fields on follow_ups.
--
-- The original follow_ups table (slice 01) tracked due_at + actioned_at + notes
-- but not whether a reminder email was sent or whether the row was auto-queued
-- on submission create. Adding two additive, NULL-tolerant columns:
--
--   * notified_at  — set by the scheduler-stack notify Lambda after SES sends
--                    the reminder. Decoupled from actioned_at so we can tell
--                    "we reminded you but you haven't acted" from "you acted
--                    before the reminder fired."
--   * auto_created — flag distinguishing the on-submission auto-queued
--                    follow-up from manually-added ones. Used by the UI to
--                    label them differently and (later) by sweeps that purge
--                    stale auto-rows when their submission moves to a
--                    terminal status.
--
-- The EventBridge Scheduler schedule name is deterministic (followup-<id>),
-- so no column is needed to track it — create/delete by computed name.

ALTER TABLE follow_ups
    ADD COLUMN notified_at  TIMESTAMP NULL DEFAULT NULL AFTER actioned_at,
    ADD COLUMN auto_created BOOL      NOT NULL DEFAULT FALSE AFTER notes;