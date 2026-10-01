"""Semua SQL INFORMATION_SCHEMA di satu tempat. {project} dan {region} diisi dari config; nilai lain lewat parameter."""

STORAGE = """
SELECT table_schema AS dataset, table_name AS table, total_rows, total_logical_bytes, active_logical_bytes,
       long_term_logical_bytes, total_physical_bytes, storage_last_modified_time
FROM `{project}`.`{region}`.INFORMATION_SCHEMA.TABLE_STORAGE
WHERE NOT deleted AND NOT STARTS_WITH(table_schema, '_')
"""

# Job selesai dalam jendela waktu. Job SCRIPT induk dibuang: biayanya sudah tercatat di job anak (menghindari hitung ganda).
JOBS = """
SELECT job_id, creation_time, user_email, job_type, statement_type, state, cache_hit,
       IFNULL(total_bytes_billed, 0) AS bytes_billed, IFNULL(total_bytes_processed, 0) AS bytes_processed,
       IFNULL(total_slot_ms, 0) AS slot_ms, query,
       ARRAY(SELECT AS STRUCT r.project_id, r.dataset_id, r.table_id FROM UNNEST(referenced_tables) r) AS referenced_tables,
       IF(destination_table.table_id IS NULL, NULL,
          STRUCT(destination_table.project_id AS project_id, destination_table.dataset_id AS dataset_id,
                 destination_table.table_id AS table_id)) AS destination_table,
       ARRAY(SELECT AS STRUCT l.key, l.value FROM UNNEST(labels) l) AS labels,
       error_result.reason AS error_reason
FROM `{project}`.`{region}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
  AND job_type = 'QUERY' AND IFNULL(statement_type, '') != 'SCRIPT' AND state = 'DONE'
"""

# Skema per dataset: kolom, tipe, deskripsi, posisi partisi & cluster.
COLUMNS = """
SELECT c.table_name AS table, c.column_name AS column, c.data_type, c.is_nullable,
       c.is_partitioning_column, c.clustering_ordinal_position, p.description
FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.COLUMNS c
LEFT JOIN `{project}`.`{dataset}`.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS p
  ON p.table_name = c.table_name AND p.column_name = c.column_name AND p.field_path = c.column_name
ORDER BY c.table_name, c.ordinal_position
"""

TABLES = """
SELECT t.table_name AS table, t.table_type, t.creation_time,
       (SELECT o.option_value FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.TABLE_OPTIONS o
         WHERE o.table_name = t.table_name AND o.option_name = 'description') AS description_raw
FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.TABLES t
"""

VIEWS = """
SELECT table_name AS table, view_definition FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.VIEWS
"""

# Cek DQ yang butuh scan data: null per kolom dan duplikat key. Dibatasi maximum_bytes_billed + dry run.
def dq_profile(table: str, not_null: list[str], unique_key: list[str]) -> str:
    nulls = ", ".join(f"COUNTIF(`{c}` IS NULL) AS null__{c}" for c in not_null) or "0 AS no_null_check"
    key = ", ".join(f"`{c}`" for c in unique_key)
    dup = f"(SELECT COUNT(*) FROM (SELECT {key} FROM `{table}` GROUP BY {key} HAVING COUNT(*) > 1))" if unique_key else "0"
    return f"SELECT COUNT(*) AS row_count, {nulls}, {dup} AS duplicate_keys FROM `{table}`"
