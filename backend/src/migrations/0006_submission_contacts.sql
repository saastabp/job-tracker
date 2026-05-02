-- Slice 08: many-to-many between submissions and contacts.
--
-- A submission can be associated with N contacts (recruiter who routed it,
-- HM, panel members, internal referrer, etc.); a contact can be associated
-- with N submissions (you've talked to the same recruiter about three roles
-- at the same company). Until now the relationship lived in free-text notes;
-- this junction makes it queryable so the SPA can render "who is on this
-- application" and the contact detail page can render "what did I send this
-- person."
--
-- Junction-table shape per `feedback_db_conventions`: `id` PK, no
-- created_at / updated_at — the two FKs already encode everything the
-- relationship row carries today. Per-link role (recruiter/HM/peer/panel)
-- is deferred per the slice plan; if it lands later it becomes a nullable
-- short_name FK to a `submission_contact_roles` catalog table (no ENUM,
-- per `project_extensibility`).
--
-- ON DELETE CASCADE on both sides because a hard-delete of either parent
-- should sweep link rows automatically. Soft-deletes (deleted_at on the
-- parent) leave the link in place; the API filters via JOINs that already
-- check the parent's deleted_at, so soft-deleted parents disappear from
-- the linked-list views without touching the junction.

CREATE TABLE submission_contacts (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    submission_id BIGINT UNSIGNED NOT NULL,
    contact_id    BIGINT UNSIGNED NOT NULL,
    UNIQUE KEY uq_submission_contacts (submission_id, contact_id),
    KEY ix_submission_contacts_contact (contact_id),
    CONSTRAINT fk_submission_contacts_submission
        FOREIGN KEY (submission_id) REFERENCES submissions (id) ON DELETE CASCADE,
    CONSTRAINT fk_submission_contacts_contact
        FOREIGN KEY (contact_id) REFERENCES contacts (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;