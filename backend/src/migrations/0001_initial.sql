-- Initial schema for job-tracker.
--
-- Conventions (see feedback_db_conventions.md / project_extensibility.md):
--   * Every table has `id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY`.
--   * First-order tables include `created_at`, `updated_at`, `deleted_at`
--     (deleted_at dormant for now, present so future soft-delete needs no migration).
--   * Catalog (lookup) tables follow the standard catalog shape:
--     `id`, `short_name UNIQUE`, `description`, timestamps, `deleted_at`.
--   * No ENUM columns anywhere — referenced via FK to a catalog table — except
--     `targets.cadence` (daily/weekly is unlikely to extend; a 2-row catalog is overkill).
--   * `schema_migrations` is special: tracked by the runner, not a domain table.

SET NAMES utf8mb4;

-- ----------------------------------------------------------------------------
-- Migration tracking
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    VARCHAR(255) NOT NULL PRIMARY KEY,
    applied_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
-- Catalog tables
-- ----------------------------------------------------------------------------

CREATE TABLE target_types (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    short_name  VARCHAR(64)  NOT NULL,
    description VARCHAR(255) NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at  TIMESTAMP    NULL DEFAULT NULL,
    UNIQUE KEY uq_target_types_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO target_types (short_name, description) VALUES
    ('submissions',        'Submissions'),
    ('personal_outreach',  'Personal Outreach'),
    ('recruiter_outreach', 'Recruiter Outreach'),
    ('follow_ups',         'Follow-ups');

CREATE TABLE submission_statuses (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    short_name  VARCHAR(64)  NOT NULL,
    description VARCHAR(255) NOT NULL,
    is_terminal BOOL         NOT NULL DEFAULT FALSE,
    sort_order  INT          NOT NULL DEFAULT 0,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at  TIMESTAMP    NULL DEFAULT NULL,
    UNIQUE KEY uq_submission_statuses_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO submission_statuses (short_name, description, is_terminal, sort_order) VALUES
    ('applied',      'Applied',      FALSE, 10),
    ('responded',    'Responded',    FALSE, 20),
    ('interviewing', 'Interviewing', FALSE, 30),
    ('offer',        'Offer',        TRUE,  40),
    ('rejected',     'Rejected',     TRUE,  50),
    ('ghosted',      'Ghosted',      TRUE,  60);

CREATE TABLE contact_kinds (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    short_name  VARCHAR(64)  NOT NULL,
    description VARCHAR(255) NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at  TIMESTAMP    NULL DEFAULT NULL,
    UNIQUE KEY uq_contact_kinds_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO contact_kinds (short_name, description) VALUES
    ('personal',  'Personal'),
    ('recruiter', 'Recruiter');

CREATE TABLE response_classifications (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    short_name  VARCHAR(64)  NOT NULL,
    description VARCHAR(255) NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at  TIMESTAMP    NULL DEFAULT NULL,
    UNIQUE KEY uq_response_classifications_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO response_classifications (short_name, description) VALUES
    ('rejection',          'Rejection'),
    ('interview_invite',   'Interview Invite'),
    ('auto_ack',           'Auto-Acknowledgement'),
    ('recruiter_outreach', 'Recruiter Outreach'),
    ('other',              'Other');

-- ----------------------------------------------------------------------------
-- Entity tables
-- ----------------------------------------------------------------------------

CREATE TABLE users (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    cognito_sub     VARCHAR(255) NOT NULL,
    email           VARCHAR(320) NOT NULL,
    display_name    VARCHAR(255) NULL,
    follow_up_days  INT          NOT NULL DEFAULT 7,
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at      TIMESTAMP    NULL DEFAULT NULL,
    UNIQUE KEY uq_users_cognito_sub (cognito_sub)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE companies (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id    BIGINT UNSIGNED NOT NULL,
    name       VARCHAR(255) NOT NULL,
    notes      TEXT         NULL,
    created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP    NULL DEFAULT NULL,
    CONSTRAINT fk_companies_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE resumes (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id     BIGINT UNSIGNED NOT NULL,
    is_master   BOOL         NOT NULL DEFAULT FALSE,
    title       VARCHAR(255) NULL,
    summary     TEXT         NULL,
    file_s3_key VARCHAR(1024) NULL,
    parsed_text MEDIUMTEXT   NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at  TIMESTAMP    NULL DEFAULT NULL,
    CONSTRAINT fk_resumes_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE submissions (
    id                   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id              BIGINT UNSIGNED NOT NULL,
    company_id           BIGINT UNSIGNED NULL,
    resume_id            BIGINT UNSIGNED NULL,
    submission_status_id BIGINT UNSIGNED NOT NULL,
    role_title           VARCHAR(255) NULL,
    submitted_on         DATE         NULL,
    notes                TEXT         NULL,
    tailored_title       VARCHAR(255) NULL,
    tailored_summary     TEXT         NULL,
    jd_url               VARCHAR(2048) NULL,
    created_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at           TIMESTAMP    NULL DEFAULT NULL,
    CONSTRAINT fk_submissions_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_submissions_company
        FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE SET NULL,
    CONSTRAINT fk_submissions_resume
        FOREIGN KEY (resume_id) REFERENCES resumes (id) ON DELETE SET NULL,
    CONSTRAINT fk_submissions_status
        FOREIGN KEY (submission_status_id) REFERENCES submission_statuses (id),
    KEY ix_submissions_user_submitted (user_id, submitted_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE responses (
    id                         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    submission_id              BIGINT UNSIGNED NOT NULL,
    response_classification_id BIGINT UNSIGNED NOT NULL,
    received_at                TIMESTAMP    NOT NULL,
    from_email                 VARCHAR(320) NULL,
    subject                    VARCHAR(998) NULL,
    body_text                  MEDIUMTEXT   NULL,
    raw_email_s3_key           VARCHAR(1024) NULL,
    created_at                 TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                 TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at                 TIMESTAMP    NULL DEFAULT NULL,
    CONSTRAINT fk_responses_submission
        FOREIGN KEY (submission_id) REFERENCES submissions (id) ON DELETE CASCADE,
    CONSTRAINT fk_responses_classification
        FOREIGN KEY (response_classification_id) REFERENCES response_classifications (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE follow_ups (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    submission_id BIGINT UNSIGNED NOT NULL,
    due_at        TIMESTAMP NOT NULL,
    actioned_at   TIMESTAMP NULL DEFAULT NULL,
    notes         TEXT      NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at    TIMESTAMP NULL DEFAULT NULL,
    CONSTRAINT fk_follow_ups_submission
        FOREIGN KEY (submission_id) REFERENCES submissions (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE jd_snapshots (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    submission_id BIGINT UNSIGNED NOT NULL,
    s3_key        VARCHAR(1024) NOT NULL,
    captured_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_url    VARCHAR(2048) NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at    TIMESTAMP NULL DEFAULT NULL,
    UNIQUE KEY uq_jd_snapshots_submission (submission_id),
    CONSTRAINT fk_jd_snapshots_submission
        FOREIGN KEY (submission_id) REFERENCES submissions (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE contacts (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id         BIGINT UNSIGNED NOT NULL,
    contact_kind_id BIGINT UNSIGNED NOT NULL,
    name            VARCHAR(255)  NOT NULL,
    email           VARCHAR(320)  NULL,
    linkedin_url    VARCHAR(1024) NULL,
    company_id      BIGINT UNSIGNED NULL,
    notes           TEXT          NULL,
    created_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at      TIMESTAMP     NULL DEFAULT NULL,
    CONSTRAINT fk_contacts_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_contacts_kind
        FOREIGN KEY (contact_kind_id) REFERENCES contact_kinds (id),
    CONSTRAINT fk_contacts_company
        FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE contact_outreach (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id     BIGINT UNSIGNED NOT NULL,
    contact_id  BIGINT UNSIGNED NOT NULL,
    outreach_at TIMESTAMP NOT NULL,
    notes       TEXT      NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at  TIMESTAMP NULL DEFAULT NULL,
    CONSTRAINT fk_contact_outreach_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_contact_outreach_contact
        FOREIGN KEY (contact_id) REFERENCES contacts (id) ON DELETE CASCADE,
    KEY ix_contact_outreach_user_at (user_id, outreach_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE targets (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id        BIGINT UNSIGNED NOT NULL,
    target_type_id BIGINT UNSIGNED NOT NULL,
    cadence        ENUM('daily', 'weekly') NOT NULL,
    goal_count     INT UNSIGNED NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at     TIMESTAMP NULL DEFAULT NULL,
    UNIQUE KEY uq_targets_user_type_cadence (user_id, target_type_id, cadence),
    CONSTRAINT fk_targets_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_targets_type
        FOREIGN KEY (target_type_id) REFERENCES target_types (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
