-- Slice 09: Gmail integration (read + write).
--
-- Four additive changes that wire submissions and responses to Gmail
-- threads, plus a new per-user credentials table for OAuth refresh tokens
-- and polling state, plus CSRF state columns on users for the OAuth flow.
--
-- 1. submissions.gmail_thread_id — links a submission to a Gmail thread.
--    Nullable: pre-09 submissions and any submission the user never bothers
--    to link carry NULL and never get auto-attached responses. Two ways the
--    column gets populated:
--      - App-originated send (slice 09 compose handler) writes it directly
--        from the threadId returned by users.messages.send.
--      - Manual paste of any RFC 822 Message-ID from the thread; the admin
--        handler resolves Message-ID → threadId via Gmail search and writes
--        it here. Used for submissions sent through another mail client and
--        for recruiter-originated chains.
--
-- 2. responses.gmail_message_id — idempotency anchor for the inbound poller.
--    The unique key is what makes "same message arriving twice during a
--    retry" produce a single response row, not two. Nullable for the same
--    reason: pre-09 responses (none today, but the column type has to allow
--    backfill of any kind).
--
-- 3. gmail_credentials — one row per user who has connected their gmail.
--    Refresh token stored as a KMS-encrypted blob; the gmail stack's KMS key
--    id is baked into the Lambdas' env. Soft-delete on disconnect (keeps
--    the audit trail, lets the SPA show "last connected"). The scopes
--    column is populated from Google's actual token response (NOT a
--    hard-coded string) so partial grants (user denies a scope on the
--    consent screen) are reflected accurately. last_history_id is the
--    poller's pagination cursor — VARCHAR(64) to match Gmail's API contract.
--
-- No catalog table for `scopes`. OAuth scope strings are an external
-- system's concept and pinning them to a catalog adds friction every time
-- Google changes scope strings. Stored as a denormalized space-separated
-- string (Google's own format).

-- 4. users.gmail_oauth_state + .gmail_oauth_state_expires_at — CSRF token
--    storage for the OAuth start → callback round-trip. The start handler
--    is Cognito-authenticated and writes a random 32-byte token + a 10-min
--    expiry to the requesting user's row. The callback handler is NOT
--    Cognito-authenticated (Google's 302 redirect carries no Authorization
--    header), so it identifies the user by looking up which row has the
--    matching state token. The columns are cleared on successful callback
--    (one-time use) and naturally expire if the user abandons the flow.
ALTER TABLE users
    ADD COLUMN gmail_oauth_state VARCHAR(64) NULL,
    ADD COLUMN gmail_oauth_state_expires_at TIMESTAMP NULL;

ALTER TABLE submissions
    ADD COLUMN gmail_thread_id VARCHAR(64) NULL AFTER notes,
    ADD UNIQUE KEY uq_submissions_gmail_thread (gmail_thread_id);

ALTER TABLE responses
    ADD COLUMN gmail_message_id VARCHAR(64) NULL AFTER raw_email_s3_key,
    ADD UNIQUE KEY uq_responses_gmail_message (gmail_message_id);

CREATE TABLE gmail_credentials (
    id                       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id                  BIGINT UNSIGNED NOT NULL,
    gmail_address            VARCHAR(320)    NOT NULL,
    refresh_token_ciphertext VARBINARY(2048) NOT NULL,
    scopes                   VARCHAR(1024)   NOT NULL,
    last_history_id          VARCHAR(64)     NULL,
    last_polled_at           TIMESTAMP       NULL DEFAULT NULL,
    created_at               TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at               TIMESTAMP       NULL DEFAULT NULL,
    UNIQUE KEY uq_gmail_credentials_user (user_id),
    CONSTRAINT fk_gmail_credentials_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;