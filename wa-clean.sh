#!/usr/bin/env bash
# wa-clean.sh — delete dangling references from a decrypted msgstore database.
# Usage: ./wa-clean.sh decrypted.db work.db
set -euo pipefail

SRC="${1:?Usage: $0 <decrypted msgstore> <output>}"
DST="${2:?Usage: $0 <decrypted msgstore> <output>}"

cp "$SRC" "$DST"

BEFORE="$(sqlite3 "$DST" 'SELECT COUNT(*) FROM message;')"
echo "Messages before: $BEFORE"

# 1. Mark the messages whose chat no longer exists.
sqlite3 "$DST" "
DROP TABLE IF EXISTS bad_msgs;
CREATE TABLE bad_msgs AS
SELECT _id FROM message
WHERE (chat_row_id NOT IN (SELECT _id FROM chat) OR message_type IS NULL)
  AND key_id <> '-1';"

BAD="$(sqlite3 "$DST" 'SELECT COUNT(*) FROM bad_msgs;')"
echo "Messages to delete: $BAD"

if [ "$BAD" = "0" ]; then
  echo "Nothing to clean for this pattern."
fi

CLEAN="cleanup.sql"
: > "$CLEAN"

# 2. Delete the child rows of those messages.
sqlite3 -noheader "$DST" "
SELECT 'DELETE FROM \"' || m.name || '\" WHERE ' || p.name || ' IN (SELECT _id FROM bad_msgs);'
FROM sqlite_master m
JOIN pragma_table_info(m.name) p
WHERE m.type = 'table'
  AND p.name IN ('message_row_id', 'parent_message_row_id')
  AND m.name NOT IN ('chat', 'message', 'bad_msgs');" >> "$CLEAN"

# 3. Delete the messages.
echo 'DELETE FROM message WHERE _id IN (SELECT _id FROM bad_msgs);' >> "$CLEAN"

# 4. General sweep: any child row that points to a message that does not exist.
sqlite3 -noheader "$DST" "
SELECT 'DELETE FROM \"' || m.name || '\" WHERE ' || p.name || ' > 0 AND ' || p.name || ' NOT IN (SELECT _id FROM message);'
FROM sqlite_master m
JOIN pragma_table_info(m.name) p
WHERE m.type = 'table'
  AND p.name IN ('message_row_id', 'parent_message_row_id')
  AND m.name NOT IN ('chat', 'message', 'bad_msgs');" >> "$CLEAN"

echo 'DROP TABLE bad_msgs;' >> "$CLEAN"

# 5. Safety control: never touch the chat table.
if grep -q 'DELETE FROM "chat"' "$CLEAN"; then
  echo "ABORTED: the script generated a DELETE against the chat table." >&2
  exit 1
fi

echo "Statements generated: $(grep -c DELETE "$CLEAN")"

# 6. Apply.
sqlite3 "$DST" < "$CLEAN"

# 7. Verify.
echo
echo "=== Verification ==="
sqlite3 -header -column "$DST" "
SELECT (SELECT COUNT(*) FROM message WHERE chat_row_id NOT IN (SELECT _id FROM chat) AND key_id <> '-1') AS orphan_msgs,
       (SELECT COUNT(*) FROM message_media WHERE message_row_id NOT IN (SELECT _id FROM message)) AS orphan_media,
       (SELECT COUNT(*) FROM message) AS messages_now;"
sqlite3 "$DST" "PRAGMA integrity_check;"

AFTER="$(sqlite3 "$DST" 'SELECT COUNT(*) FROM message;')"
echo "Messages: $BEFORE -> $AFTER (difference: $((BEFORE - AFTER)))"

sqlite3 "$DST" "VACUUM;"
echo "Done: $DST"
