-- Slice 10: extend contact_outreach to track email-sourced events.
--
-- Five additive nullable columns. Manually-logged rows (existing UX) keep
-- working with all five NULL. Email-sourced rows (created by the slice 10
-- compose handler on outbound, and by the poller on inbound) populate the
-- columns with content fetched from Gmail.
--
-- The unique key on gmail_message_id is the idempotency primitive
-- mirroring uq_responses_gmail_message on the responses table. Together
-- they ensure no duplicate inserts during poller retries OR when a
-- thread maps to both a submission and a contact (each table has its
-- own unique key, so a single Gmail message produces at most one row
-- in each).
--
-- Fork 1 decision (locked 2026-05-05): inbound-row contact resolution
-- picks the most-recent existing contact_outreach row's contact_id when
-- a thread has rows for multiple contacts. Keeping the unique key
-- globally on gmail_message_id (rather than scoped to
-- (contact_id, gmail_message_id)) reflects that "one Gmail message →
-- one contact_outreach row" model.
--
-- Index on gmail_thread_id supports the poller's per-cycle UNION
-- enumeration (submissions ∪ contact_outreach) and the
-- thread → contact_id lookup it does for each inbound message.

ALTER TABLE contact_outreach
    ADD COLUMN gmail_thread_id  VARCHAR(64)  NULL AFTER notes,
    ADD COLUMN gmail_message_id VARCHAR(64)  NULL AFTER gmail_thread_id,
    ADD COLUMN subject          VARCHAR(998) NULL AFTER gmail_message_id,
    ADD COLUMN body_text        MEDIUMTEXT   NULL AFTER subject,
    ADD COLUMN from_email       VARCHAR(320) NULL AFTER body_text,
    ADD UNIQUE KEY uq_contact_outreach_gmail_message (gmail_message_id),
    ADD KEY ix_contact_outreach_gmail_thread (gmail_thread_id);