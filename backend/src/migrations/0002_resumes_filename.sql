-- Slice 04: track the original uploaded filename so downloads keep their name.
--
-- The S3 key already stores the filename suffix, but pulling it out of the key
-- to drive a Content-Disposition header on the presigned GET is awkward. A
-- dedicated column is simpler and survives any future key-layout change.
--
-- Additive, NULL-tolerant — fully backward compatible with rows created
-- before this column existed (no rows yet, but the property holds).

ALTER TABLE resumes
    ADD COLUMN original_filename VARCHAR(255) NULL AFTER file_s3_key;