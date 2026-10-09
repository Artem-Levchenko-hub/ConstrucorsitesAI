-- Read-only, sanitized assessment. Run on the authorized production database
-- before 0081, under the documented generation/publication drain.
-- Returns counts only: no bot IDs, owners, tokens, projects or customer rows.
BEGIN TRANSACTION READ ONLY;
SELECT
    (SELECT COUNT(*) FROM max_integrations) AS bindings,
    (SELECT COUNT(*) FROM max_integrations WHERE bot_id IS NULL) AS legacy_unidentified,
    COUNT(*) AS duplicate_bot_groups,
    COALESCE(SUM(binding_count), 0) AS duplicate_binding_rows
FROM (
    SELECT COUNT(*) AS binding_count
    FROM max_integrations
    WHERE bot_id IS NOT NULL
    GROUP BY bot_id
    HAVING COUNT(*) > 1
) duplicates;
ROLLBACK;
