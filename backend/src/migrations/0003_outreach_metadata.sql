-- Slice 05: outreach metadata.
--
-- Adds:
--   * outreach_methods catalog (email / linkedin / phone / in_person / other)
--   * outreach_directions catalog (outbound / inbound)
--   * contacts.primary_method_id   — the contact's preferred outreach channel
--   * contact_outreach.outreach_method_id    — how each event happened
--   * contact_outreach.outreach_direction_id — outbound vs inbound (NOT NULL)
--
-- Existing rows: none expected (slice 05 is the first to write to
-- contact_outreach), but the migration handles the case anyway by
-- backfilling direction → 'outbound' before tightening to NOT NULL.

SET NAMES utf8mb4;

-- ----------------------------------------------------------------------------
-- New catalogs
-- ----------------------------------------------------------------------------

CREATE TABLE outreach_methods (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    short_name  VARCHAR(64)  NOT NULL,
    description VARCHAR(255) NOT NULL,
    sort_order  INT          NOT NULL DEFAULT 0,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at  TIMESTAMP    NULL DEFAULT NULL,
    UNIQUE KEY uq_outreach_methods_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO outreach_methods (short_name, description, sort_order) VALUES
    ('email',     'Email',       10),
    ('linkedin',  'LinkedIn',    20),
    ('phone',     'Phone',       30),
    ('in_person', 'In person',   40),
    ('other',     'Other',       90);

CREATE TABLE outreach_directions (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    short_name  VARCHAR(64)  NOT NULL,
    description VARCHAR(255) NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at  TIMESTAMP    NULL DEFAULT NULL,
    UNIQUE KEY uq_outreach_directions_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO outreach_directions (short_name, description) VALUES
    ('outbound', 'Outbound'),
    ('inbound',  'Inbound');

-- ----------------------------------------------------------------------------
-- Contacts: primary method
-- ----------------------------------------------------------------------------

ALTER TABLE contacts
    ADD COLUMN primary_method_id BIGINT UNSIGNED NULL AFTER linkedin_url,
    ADD CONSTRAINT fk_contacts_primary_method
        FOREIGN KEY (primary_method_id) REFERENCES outreach_methods (id);

-- ----------------------------------------------------------------------------
-- contact_outreach: per-event method + direction
-- ----------------------------------------------------------------------------

ALTER TABLE contact_outreach
    ADD COLUMN outreach_method_id    BIGINT UNSIGNED NULL AFTER outreach_at,
    ADD COLUMN outreach_direction_id BIGINT UNSIGNED NULL AFTER outreach_method_id;

-- Backfill direction for any rows that already exist (defensive — none expected).
UPDATE contact_outreach co
JOIN outreach_directions od ON od.short_name = 'outbound'
SET co.outreach_direction_id = od.id
WHERE co.outreach_direction_id IS NULL;

ALTER TABLE contact_outreach
    MODIFY COLUMN outreach_direction_id BIGINT UNSIGNED NOT NULL,
    ADD CONSTRAINT fk_contact_outreach_method
        FOREIGN KEY (outreach_method_id)    REFERENCES outreach_methods (id),
    ADD CONSTRAINT fk_contact_outreach_direction
        FOREIGN KEY (outreach_direction_id) REFERENCES outreach_directions (id);