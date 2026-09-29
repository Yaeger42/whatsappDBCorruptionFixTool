#!/usr/bin/env bash
# wa-repack.sh — encrypt a cleaned database and verify the result.
# Usage: ./wa-repack.sh encrypted_backup.key work.db msgstore.ORIGINAL.crypt15 msgstore.NEW.crypt15
set -euo pipefail

KEY="${1:?}"
PLAIN="${2:?}"
REF="${3:?}"
OUT="${4:?}"

rm -f "$OUT" roundtrip.db

# --reference copies the IV, the header, and the compression level from the real backup.
waencrypt --reference "$REF" "$KEY" "$PLAIN" "$OUT"

echo
echo "=== Headers: these must be identical ==="
wainfo "$REF"
wainfo "$OUT"

echo
echo "=== Round trip ==="
wadecrypt "$KEY" "$OUT" roundtrip.db

if cmp -s "$PLAIN" roundtrip.db; then
  echo "OK: the encrypted file decrypts to exactly $PLAIN"
else
  echo "FAILED: the decrypted output does not match. Do not use $OUT." >&2
  exit 1
fi

ls -l "$REF" "$OUT"
