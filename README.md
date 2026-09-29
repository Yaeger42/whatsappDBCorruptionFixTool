# whatsapp DB Corruption Fix-Tool
This is a comprehensive guide on how to use a set of tools for allowing the migration of android to whatsapp when the progress bar gets stuck at a certain percentage (like 47%) and no matter what you do it just won't move
# How to Fix the Move to iOS Error When You Transfer WhatsApp from Android to iPhone

This guide solves one specific case: **Move to iOS always fails at the same percentage** during the WhatsApp transfer, and shows *"An unknown error occurred. Please try again later."*

The cause is not the network, the cable, or the app version. The cause is corruption in the WhatsApp database on the Android phone: rows that point to records that no longer exist. The Move to iOS exporter does not handle those references, and it stops.

The fix is to open the database, delete the dangling rows, and put the database back on the phone.

---

## ⚠️ READ THIS FIRST: BACK UP YOUR MEDIA

Later this guide uses `adb shell pm clear com.whatsapp`.

**`pm clear` also deletes `/sdcard/Android/media/com.whatsapp`.** That folder holds all of your WhatsApp photos, videos, voice notes, and documents. They are gone for good. Modern Android storage uses per-file encryption, so no recovery tool gets them back.

Before anything else, copy that folder to your computer:

```bash
adb pull /sdcard/Android/media/com.whatsapp ./whatsapp-media-backup
du -sh ./whatsapp-media-backup
```

This takes time and can be several gigabytes. Do it anyway.

Also turn on device folder backup in Google Photos for `WhatsApp Images`, `WhatsApp Video`, and `WhatsApp Documents`. That protects your media independently of WhatsApp.

I learned this the hard way. Do not repeat my mistake.

---

## Is This Your Problem?

This guide applies when both conditions are true:

- The transfer fails on the WhatsApp preparing screen or sending screen.
- It fails **at the same percentage every time**.

A fixed percentage means a deterministic failure. A deterministic failure means one specific record. If the percentage changes on each attempt, your problem is the network or the power, and this guide does not apply.

### What Does Not Work

| Common advice | Result |
|---|---|
| Join the WhatsApp beta | It helps some people. It does not help many others. |
| Remove the SIM from the Android phone | No change on its own. |
| Use a cable instead of Wi-Fi, or the reverse | No change when the failure is deterministic. |
| Reinstall WhatsApp | This does not clean the database. |
| Make the backup smaller | The percentage moves, but it still fails. |
| Move to iOS 3.5.0 instead of the current version | This fixes other failures, not this one. |

The old Move to iOS version is still worth a try when your failure is different. Get version 3.5.0, build 3028, from [APKMirror](https://www.apkmirror.com/apk/apple/move-to-ios/).

---

## Requirements

- A computer with macOS or Linux.
- `adb`. On macOS: `brew install android-platform-tools`.
- Python with [`wa-crypt-tools`](https://github.com/ElDavoo/wa-crypt-tools): `python -m pip install wa-crypt-tools`.
- `sqlite3`. It comes with macOS and with most Linux distributions.
- USB debugging turned on, on the Android phone.
- Free disk space: about three times the size of your WhatsApp backup.

---

## Step 1: Turn On End-to-End Encrypted Backups

Without this step you cannot decrypt the database.

1. In WhatsApp, go to **Settings > Chats > Chat backup > End-to-end encrypted backup**.
2. Turn it on and select the **64-digit key** option, not the password option.
3. Save the key: a screenshot, a photo, and your password manager.

> **WARNING:** If you lose the 64-digit key, you lose access to all of your backups. There is no recovery.

4. Make a full manual backup, with your chats up to date.

The password does not decrypt anything. It only protects the key on the WhatsApp servers. You need the 64 digits.

---

## Step 2: Back Everything Up

```bash
# The media (read the warning at the top)
adb pull /sdcard/Android/media/com.whatsapp ./whatsapp-media-backup

# The encrypted database
adb pull /sdcard/Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15 msgstore.ORIGINAL.crypt15

# See what else is in the folder
adb shell 'ls -l /sdcard/Android/media/com.whatsapp/WhatsApp/Databases/'
```

If you see a `.crypt14` file, pull it too. Those backups are older than end-to-end encryption, and they restore **without** the 64-digit key. That file is your cheapest safety net.

---

## Step 3: Decrypt the Database

```bash
wacreatekey --hex <your 64-digit key, lowercase, no spaces>
wadecrypt encrypted_backup.key msgstore.ORIGINAL.crypt15 decrypted.db
ls -lh decrypted.db
```

If `wadecrypt` fails, delete `encrypted_backup.key` and run `wacreatekey` again. `wa-crypt-tools` also accepts the screenshot of the key directly, when Tesseract is installed.

---

## Step 4: Diagnose

Save this as `wa-diagnose.sh`:

```bash
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
```

Run it:

```bash
chmod +x wa-diagnose.sh
./wa-diagnose.sh decrypted.db
```

### How to Read the Output

**What matters.** The dangling references block and the child rows block. Any number above zero there is real corruption. In my case there were 113 system messages that pointed to chats that no longer existed, plus 847 rows across seven tables:

```
message_text|11
message_thumbnail|140
message_media_interactive_annotation|13
message_secret|276
template_messages_metadata|75
message_media|331
message_inline_video_metadata|1
```

**What is noise.** Almost the whole media block:

| Signal | Why this is not corruption |
|---|---|
| `message_type` NULL with 1 row | This is the sentinel row that WhatsApp creates in every database. You recognize it by `_id = 1`, `chat_row_id = -1`, `key_id = '-1'`. Normal. |
| `no path` and `not transferred` | Media that you never downloaded, or that you deleted from disk. WhatsApp keeps the row. Normal. |
| `size 0 or null` and `file_size <> file_length` | Current schemas barely use `file_size`. The real value is in `file_length`. Normal. |
| `no mime type` | Many sent images store an empty `mime_type`. Normal. |

In my database those four signals added up to more than 100,000 rows. None of them had a part in the failure.

**Broken files on disk.** Also look for zero-byte media, because those do stall the transfer:

```bash
adb shell 'find /sdcard/Android/media/com.whatsapp/WhatsApp/Media -type f -size 0'
```

Ignore the `.nomedia` files: they are empty markers by design. Delete any other zero-byte file.

---

## Step 5: Clean the Database

Save this as `wa-clean.sh`:

```bash
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
```

Run it:

```bash
chmod +x wa-clean.sh
./wa-clean.sh decrypted.db work.db
```

You want to see `0`, `0`, `ok`, and a message difference equal to the number that the script announced as "to delete". Run `wa-diagnose.sh` again on `work.db`: the dangling child rows block must come out empty.

The script never touches the `chat` table. That table holds columns such as `last_message_row_id`, so it appears in searches by column name. A delete against it destroys your conversations.

---

## Step 6: Encrypt Again and Verify

Save this as `wa-repack.sh`:

```bash
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
```

Run it:

```bash
chmod +x wa-repack.sh
./wa-repack.sh encrypted_backup.key work.db msgstore.ORIGINAL.crypt15 msgstore.NEW.crypt15
```

Two notes about the result.

`wainfo` prints header fields only, and it does not include the file size. Two identical outputs, with the same IV, are exactly what you want: `--reference` reproduced the phone header byte for byte.

**The new file can come out larger than the original.** Mine went from 182 MB to 190 MB, even after I deleted rows. `VACUUM` repacks the SQLite pages, and that changes how well zlib compresses them. This is not a symptom of anything. The round trip check is what decides.

---

## Step 7: Put the Database Back on the Phone

Move to iOS does **not** read the `.crypt15` file. It reads the live database, at `/data/data/com.whatsapp/databases/msgstore.db`, which you cannot reach without root. So you must make WhatsApp restore your file.

### 7a. Delete the Google Drive Backup

WhatsApp prefers the cloud. While a Drive backup exists, WhatsApp never offers you the local one.

1. In WhatsApp: **Settings > Chats > Chat backup > Automatic backups > Off**.
2. In the Google Drive app: menu > **Backups** > the three-dot menu next to WhatsApp > **Delete backup**.
3. Optional and recommended, from `drive.google.com`: gear > Settings > **Manage apps** > WhatsApp Messenger > Options > **Disconnect from Drive** and **Delete hidden app data**.

### 7b. Move the Other Backups Aside

If the `Databases` folder holds several files, WhatsApp can select the wrong one. A `.crypt14` file is the dangerous case, because it restores with no key prompt and the mistake passes unnoticed.

Move those files **outside** `Android/media`, to a place that `pm clear` does not reach:

```bash
adb shell 'mkdir -p /sdcard/wa-db-aside'
adb shell 'mv /sdcard/Android/media/com.whatsapp/WhatsApp/Databases/* /sdcard/wa-db-aside/'
```

### 7c. The Correct Order

This order matters. If you push the file before `pm clear`, you lose it.

```bash
# 1. Reset the app to its first-run state.
#    This DELETES /sdcard/Android/media/com.whatsapp. The Step 2 backup is mandatory.
adb shell pm clear com.whatsapp
```

Now, **on the phone**: open WhatsApp, accept the terms, and stop at the screen that asks for your phone number. Do not type it. This makes WhatsApp create its folder again with the correct ownership.

```bash
# 2. Stop the app and put your clean database in place.
adb shell am force-stop com.whatsapp
adb shell 'mkdir -p /sdcard/Android/media/com.whatsapp/WhatsApp/Databases'
adb push msgstore.NEW.crypt15 /sdcard/Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15

# 3. Check the result.
adb shell 'ls -l /sdcard/Android/media/com.whatsapp/WhatsApp/Databases/'
```

The output must show one file, with the size of your `msgstore.NEW.crypt15` and owner `u0_aNNN media_rw`. If the owner is `shell`, WhatsApp cannot read it.

### 7d. Restore

On the phone:

1. Open WhatsApp and verify your phone number.
2. When WhatsApp asks for permission to search your Google account for backups, tap **Skip**. Permission links Drive again.
3. If the screen about a transfer from your old phone appears, decline it with the secondary option.
4. When WhatsApp offers the local backup, tap restore.
5. Enter the 64-digit key.

You restore nothing by hand. WhatsApp reads the file that you put in place.

If WhatsApp takes you straight to the name and photo screen with no backup offer, complete nothing. Check the file owner, and check that the `Databases` folder holds one `.crypt15` file only.

---

## Step 8: Get Media Back Before You Transfer

This step comes after Step 7, not before it. Step 7 empties the media folder, and this step fills it again. A push before `pm clear` is lost, and the download arrows appear only for files that are missing from disk.

After the restore, WhatsApp shows a download arrow over media that is not on disk. Recent enough messages download again from the server. Old ones say that the file is no longer available.

Go through the chats that matter to you and download what you want to keep. Everything that you download now travels to the iPhone. Everything else stays absent for good.

If you backed up the media folder in Step 2, you can put it back:

```bash
adb push ./whatsapp-media-backup/WhatsApp/Media /sdcard/Android/media/com.whatsapp/WhatsApp/
```

---

## Step 9: The Transfer

1. Erase the iPhone: **Settings > General > Transfer or Reset iPhone > Erase All Content and Settings**. The WhatsApp transfer is possible during the initial setup only.
2. Connect both phones to power.
3. Turn off automatic Wi-Fi connection on the Android phone.
4. Start the iPhone setup and pair with Move to iOS.
5. Select WhatsApp on the data screen.
6. **Keep both screens awake for the whole process.** Tap the iPhone screen every minute. When a screen turns off, Android throttles the Wi-Fi chip and suspends the connection between the two phones.
7. Cancel nothing. The bar moves very slowly and the time estimate is not reliable.

One sign that it goes well: the time estimate on the iPhone and the one on the Android phone agree. If the two estimates drift apart, that attempt is already lost.

---

## Step 10: Activate WhatsApp on the iPhone

1. Finish the iPhone setup **before** you open anything related to WhatsApp.
2. Install WhatsApp from the App Store.
3. Log in with the same phone number that you used on Android. It must match character for character, country code included.
4. When WhatsApp asks, tap **Start** and wait for the import to complete.

If WhatsApp asks for a verification code and sends it to the old phone, where it already logged you out, log in on the Android phone again to get the code. The data already transferred to the iPhone stays there.

Do not delete WhatsApp from the Android phone until you see the complete chats on the iPhone.

---

## If It Fails Again

**At the same exact percentage.** The corruption was something else. Go back to `wa-diagnose.sh` and look for other patterns. The database is already decrypted and you can query whatever you want.

**At a different percentage.** This is a good sign. The exporter moved past the record that stalled it, and there is another one to clean. Repeat the cycle.

**With the same error but at 0%.** That is a different failure. There, the WhatsApp beta and Move to iOS 3.5.0 are worth a try.

### Useful Queries to Dig Further

```sql
-- Messages in one chat, around a suspect message
WITH bad AS (SELECT _id, chat_row_id, timestamp FROM message WHERE _id = <ID>)
SELECT m._id,
       datetime(m.timestamp/1000, 'unixepoch', 'localtime') AS when_local,
       m.from_me, m.message_type,
       substr(m.text_data, 1, 80) AS text
FROM message m, bad
WHERE m.chat_row_id = bad.chat_row_id
  AND m.timestamp BETWEEN bad.timestamp - 86400000 AND bad.timestamp + 86400000
ORDER BY m.timestamp;

-- Which conversation a message belongs to
SELECT c._id, j.raw_string, c.subject
FROM chat c JOIN jid j ON j._id = c.jid_row_id
WHERE c._id = (SELECT chat_row_id FROM message WHERE _id = <ID>);

-- Media inventory per chat, useful when you ask people to send files again
SELECT j.raw_string, COUNT(*) AS n, mm.mime_type
FROM message_media mm
JOIN message m ON m._id = mm.message_row_id
JOIN chat c ON c._id = m.chat_row_id
JOIN jid j ON j._id = c.jid_row_id
GROUP BY j.raw_string, mm.mime_type
ORDER BY n DESC;
```

---

## Backups After You Migrate

Set up both layers the same day. Do not leave this for later:

- **iPhone:** WhatsApp Settings > Chats > Chat Backup, to iCloud.
- **Media, on any platform:** let iCloud Photos or Google Photos keep the files. A media copy that does not depend on WhatsApp is the only thing that survives an accidental delete.

---

## Credits

- The idea that a fixed percentage points to one corrupt record, and the method of decrypting the database to find it, comes from [this guide by rkharsan](https://gist.github.com/rkharsan/2aa3c56b9aa1107439dd8881f680abe3). In that case the record was a text message with a null `text_data`.
- [`wa-crypt-tools`](https://github.com/ElDavoo/wa-crypt-tools) by ElDavoo makes all of this possible.
- The finding that Move to iOS 3.5.0 fixes other transfer failures comes from [r/ios](https://www.reddit.com/r/ios/comments/1cxu9zc/if_you_are_moving_from_android_to_ios_and_having/).
- Keeping both screens awake comes from [r/iphone](https://www.reddit.com/r/iphone/comments/1uexn0h/the_move_to_ios_app_keeps_failing_on_large/).
- Local restore of a `crypt15` backup is documented on [Nicola Inchingolo's blog](https://www.inginc.eu/2025/07/02/restore-whatsapp-local-backup-dbcrypt15-and-over-on-a-new-device/).

## Disclaimer

This guide modifies the WhatsApp database. WhatsApp supports none of this. The scripts always work on copies and verify the result before anything goes back to the phone, but the responsibility is yours. Back up the media folder before you start, save the 64-digit key, and keep your Drive backup until the round trip check in Step 6 passes.
