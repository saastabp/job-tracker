-- Slice 05 follow-up: phone number on contacts.
--
-- The original `contacts` table (slice 01) only had `email` + `linkedin_url`
-- on the assumption phone numbers could live in `notes`. In practice the
-- contact form expects a structured phone field. Additive, NULL-tolerant.

ALTER TABLE contacts
    ADD COLUMN phone VARCHAR(64) NULL AFTER email;