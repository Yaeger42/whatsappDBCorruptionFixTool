#!/usr/bin/env bash
# wa-diagnose.sh — find corruption in a decrypted WhatsApp msgstore database.
# Usage: ./wa-diagnose.sh decrypted.db
set -euo pipefail

DB="${1:?Usage: $0 <decrypted msgstore>}"

echo "=== SQLite integrity ==="
sqlite3 "$DB" "PRAGMA integrity_check;"

echo
echo "=== Totals ==="
sqlite3 -header -column "$DB" "
SELECT (SELECT COUNT(*) FROM message) AS messages,
       (SELECT COUNT(*) FROM chat)    AS chats,
       (SELECT COUNT(*) FROM message_media) AS media_rows;"

echo
echo "=== Message types (a single NULL here is usually the sentinel row) ==="
sqlite3 -header -column "$DB" "
SELECT message_type, COUNT(*) AS n FROM message GROUP BY message_type ORDER BY n DESC LIMIT 15;"

echo
echo "=== Dangling references (THIS is what breaks Move to iOS) ==="
sqlite3 -header -column "$DB" "
SELECT 'messages with no chat' AS problem,
       COUNT(*) AS n
FROM message
WHERE chat_row_id NOT IN (SELECT _id FROM chat) AND key_id <> '-1'
UNION ALL
SELECT 'messages with NULL type',
       COUNT(*)
FROM message
WHERE message_type IS NULL AND key_id <> '-1';"

echo
echo "=== Dangling child rows, table by table ==="
REPORT="$(mktemp)"
sqlite3 -noheader "$DB" "
SELECT 'SELECT ''' || m.name || ''' AS tbl, COUNT(*) AS n FROM \"' || m.name || '\" WHERE ' || p.name || ' > 0 AND ' || p.name || ' NOT IN (SELECT _id FROM message) UNION ALL'
FROM sqlite_master m
JOIN pragma_table_info(m.name) p
WHERE m.type = 'table'
  AND p.name IN ('message_row_id', 'parent_message_row_id')
  AND m.name NOT IN ('chat', 'message');" > "$REPORT"
echo "SELECT 'end' AS tbl, 0 AS n;" >> "$REPORT"
sqlite3 -noheader -separator '|' "$DB" < "$REPORT" | awk -F'|' '$2 > 0'
rm -f "$REPORT"

echo
echo "=== Media: mostly NOISE, read the guide before you worry ==="
sqlite3 -header -column "$DB" "
SELECT 'no path' AS field, COUNT(*) AS n FROM message_media WHERE file_path IS NULL OR TRIM(file_path) = ''
UNION ALL SELECT 'size 0 or null', COUNT(*) FROM message_media WHERE file_size IS NULL OR file_size <= 0
UNION ALL SELECT 'no mime type', COUNT(*) FROM message_media WHERE mime_type IS NULL OR TRIM(mime_type) = ''
UNION ALL SELECT 'file_size <> file_length', COUNT(*) FROM message_media WHERE file_size <> file_length
UNION ALL SELECT 'not transferred', COUNT(*) FROM message_media WHERE transferred = 0;"
