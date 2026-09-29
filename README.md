# How to Fix the Move to iOS Error When You Transfer WhatsApp from Android to iPhone

This guide investigates one reported case: **Move to iOS always fails at the same percentage** during the WhatsApp transfer, and shows *"An unknown error occurred. Please try again later."*

Dangling references in the WhatsApp database were the cause in the case behind this guide. A repeated failure percentage alone does not establish that diagnosis; network, storage, software, and other database problems still need to be considered.

The tools below check a decrypted copy, remove a limited set of dangling rows when the checks allow it, and prepare a new encrypted backup. They do not repair physical SQLite corruption or guarantee that WhatsApp will accept a modified backup.

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

A fixed percentage is a useful clue, not proof of a particular bad record. Diagnose a copy before deciding whether this cleanup applies. A changing percentage does not identify the cause either.

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
- Python 3.10 or newer and Git. Install the tested [`wa-crypt-tools`](https://github.com/ElDavoo/wa-crypt-tools) revision using the commands below.
- `sqlite3`. It comes with macOS and with most Linux distributions.
- USB debugging turned on, on the Android phone.
- Free disk space for the original encrypted backup, decrypted database, temporary snapshot, new encrypted backup, and media. Several times the encrypted backup size may be needed. Encryption also needs memory for the database and compressed buffers.
- A local output filesystem that supports hard links; outputs are published without overwriting existing files.

---

## Install the tools

Clone the whole repository; the shell entry points need the Python helpers beside them:

```bash
git clone https://github.com/Yaeger42/whatsappDBCorruptionFixTool.git
cd whatsappDBCorruptionFixTool
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
umask 077
```

Keep this environment active for the commands below. The dependency is pinned because repacking uses its header-preserving crypt15 API. Diagnosis and cleanup use Python's standard SQLite library.

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

```bash
bash wa-diagnose.sh decrypted.db
```

This opens the existing file read-only and prints a JSON report. Exit status `0` means the implemented checks passed; `1` means findings or an error, so read the report before continuing.

- `integrity` must contain only `ok`. Physical corruption must be handled separately.
- `bad_messages` counts missing chats or NULL message types, excluding the documented sentinel. The sentinel is recognized by its shape (`chat_row_id=-1`, `key_id='-1'`, NULL type), not by its rowid.
- `orphan_children` counts positive `message_row_id` and `parent_message_row_id` references with no matching message.
- `dangling_chat_pointers` reports positive chat pointers ending in `_message_row_id` that have no target.
- `foreign_key_errors` reports violations of declared foreign keys. Many WhatsApp relationships are implicit, so this is not a complete relationship check.

The supported schema requires `message`, `chat`, and `message_media`, with integer primary keys on `message._id` and `chat._id`. Databases missing the required tables or columns are rejected. The tools have automated synthetic tests, but no claim of compatibility with every WhatsApp version or a completed device migration.

Missing media paths, empty MIME types, or undownloaded attachments are not by themselves reasons to delete a message. These tools do not remove media files.

### Counts that look alarming and are not

Media bookkeeping produces large benign numbers. The report does not flag them, but you will meet them if you query the database yourself:

| Signal | Why this is not corruption |
|---|---|
| One message with a NULL `message_type`, `chat_row_id` of -1, and `key_id` of `-1` | The sentinel row that WhatsApp writes into every database. The tools preserve it. |
| Media rows with no `file_path`, or with `transferred = 0` | Media that you never downloaded, or that you deleted from disk. WhatsApp keeps the row. |
| Media rows with `file_size` of 0, or with `file_size` different from `file_length` | Current schemas barely use `file_size`. The real value is in `file_length`. |
| Media rows with an empty `mime_type` | Many sent images store an empty `mime_type`. |

In the database behind this guide those four signals covered more than 100,000 rows. None of them had a part in the failure.

### Broken files on the phone

These tools cannot see this one, because it lives on the phone filesystem instead of the database. Zero-byte media stalls the transfer:

```bash
adb shell 'find /sdcard/Android/media/com.whatsapp/WhatsApp/Media -type f -size 0'
```

Ignore the `.nomedia` files: they are empty markers by design. Delete any other zero-byte file.

---

## Step 5: Clean the Database

Preview the cleanup on a temporary copy first:

```bash
bash wa-clean.sh decrypted.db --dry-run
```

Then create a new output:

```bash
bash wa-clean.sh decrypted.db work.db
bash wa-diagnose.sh work.db
```

Use a new output filename on each attempt. Existing files and symlinks are rejected. The SQLite backup API includes committed WAL contents; the original is not edited. Cleanup runs in a transaction on a private temporary copy, checks its results, then publishes a file with owner-only permissions. A failed run publishes no output.

**If a selected message is still referenced by a chat pointer, cleanup stops.** The tool does not guess a replacement message, delete the chat, or silently leave a dangling pointer. Investigate that schema and relationship before proceeding.

### Findings that cleanup never repairs

Two findings have no safe repair: dangling chat pointers and violations of declared foreign keys. When your input already carries them, cleanup completes its own work and then stops, because a database with unexplained damage is not a safe input. The error names the findings. Read them, then accept them knowingly:

```bash
bash wa-clean.sh decrypted.db work.db --allow-preexisting
```

The flag accepts only the counts that the input already had. If this run increases any of them, the output is still refused. Integrity failures, remaining bad messages, and orphan child rows are never relaxed. Pass the same flag to `wa-repack.sh`, so that it accepts the database that cleanup produced.

Successful checks cover the relationships listed above, not every possible WhatsApp invariant. Keep the original encrypted backup and your media copy.

---

## Step 6: Encrypt Again and Verify

```bash
bash wa-repack.sh encrypted_backup.key work.db msgstore.ORIGINAL.crypt15 msgstore.NEW.crypt15
```

The tool authenticates the original crypt15 backup, takes a consistent SQLite snapshot of the cleaned database, and encrypts it with a **fresh random IV**. It preserves the reference header metadata, including unknown protobuf fields, and checks that only the IV changed. Never reuse an AES-GCM IV with the same key for different contents.

Verification authenticates and decompresses the new file and compares the resulting snapshot's length and SHA-256 digest. SQLite's backup API may normalize header counters, so the snapshot need not be byte-identical to the input file even when its data is unchanged. No plaintext `roundtrip.db` is left behind; private temporary files are removed on normal completion or a handled failure. Existing outputs are never overwritten.

**This is local verification, not proof that WhatsApp can restore the backup.** Retain all recovery copies until chats and media have been checked on the destination phone. An encrypted file's size can change after cleanup and compression; size alone does not establish correctness.

---

## Step 7: Put the Database Back on the Phone

Move to iOS does **not** read the `.crypt15` file. It reads the live database, at `/data/data/com.whatsapp/databases/msgstore.db`, which you cannot reach without root. So you must make WhatsApp restore your file.

### 7a. Keep a recovery path

A local round trip is not a restore test. Build your fallbacks before you clear app data.

1. Turn off automatic WhatsApp backups, so that a failed attempt cannot replace a useful backup.
2. Keep the original encrypted file and the key off the phone. Confirm that the media copy completed, inspect its files, and compare file counts and sizes against the phone. Keep a second independent copy if you can.
3. During setup, skip the Google account backup search when that option is offered. Try this first, because WhatsApp cannot read a Drive backup that it has no permission to search.

**If WhatsApp still does not offer the local backup, the Drive backup is what hides it.** WhatsApp prefers the cloud copy. In the one case behind this guide, the local restore appeared only after the Drive backup was deleted and WhatsApp was disconnected from Drive.

> **CAUTION:** Delete the Drive backup only after step 2 above is complete and verified. You cannot download a Drive backup, so it is not a copy that you can inspect. Your computer copies are.

- In the Google Drive app: menu > **Backups** > the three-dot menu next to WhatsApp > **Delete backup**.
- From `drive.google.com`: gear > Settings > **Manage apps** > WhatsApp Messenger > Options > **Disconnect from Drive**.

Do not continue to `pm clear` unless you have verified the off-device copies and have a way back if the local restore fails.

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

**At the same exact percentage.** The cause is still unresolved. Go back to the diagnostic report and investigate other causes before deleting more data.

**At a different percentage.** The failure changed, but that does not prove another record needs deleting. Investigate before repeating cleanup.

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

This guide modifies the WhatsApp database. WhatsApp supports none of this. The scripts always work on copies and verify the result before anything goes back to the phone, but the responsibility is yours. Back up the media folder before you start, save the 64-digit key, and keep your Drive backup until the destination chats and media have been verified; a local round trip alone does not establish restorability.


## Development

Run the regression suite after installing `requirements.txt`:

```bash
python3 -m unittest discover -s tests -v
```

Tests generate disposable databases and keys. They cover output collisions, failed cleanup, WAL snapshots, chat-pointer refusal, integrity checks, and authenticated crypt15 round trips with fresh IVs and preserved metadata. Run them locally with the command above. Device restore testing remains a separate step.
