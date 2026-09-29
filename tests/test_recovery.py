"""Regression tests use only generated databases and keys, never personal backups."""

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zlib
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import wa_db
import wa_repack

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = """
CREATE TABLE chat (_id INTEGER PRIMARY KEY, last_message_row_id INTEGER, display_message_row_id INTEGER);
CREATE TABLE message (_id INTEGER PRIMARY KEY, chat_row_id INTEGER, message_type INTEGER, key_id TEXT);
CREATE TABLE message_media (_id INTEGER PRIMARY KEY, message_row_id INTEGER);
INSERT INTO chat VALUES (10, 2, 2);
INSERT INTO message VALUES (1, -1, NULL, '-1'), (2, 10, 0, 'valid'), (3, 99, 0, 'orphan');
INSERT INTO message_media VALUES (1, 2), (2, 3), (3, 999);
"""


def make_database(path, extra=""):
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(SCHEMA + extra)


def rows(path, query):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(query).fetchall()


def digest(path):
    return hashlib.sha256(path.read_bytes()).digest()


class DatabaseFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / "source.db"
        self.output = self.directory / "output.db"
        make_database(self.source)

    def run_tool(self, tool, *arguments):
        env = dict(
            os.environ,
            PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        )
        return subprocess.run(
            ["bash", str(ROOT / tool), *map(str, arguments)],
            cwd=self.directory,
            env=env,
            text=True,
            capture_output=True,
        )


class RecoveryTests(DatabaseFixture):
    def assert_refused(self, extra, message):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.executescript(extra)
        before = digest(self.source)
        result = self.run_tool("wa-clean.sh", self.source, self.output)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(digest(self.source), before)
        self.assertEqual(list(self.directory.glob(".wa-*")), [])

    def test_cleanup_preserves_source_sentinel_and_chat(self):
        before = digest(self.source)
        result = self.run_tool("wa-clean.sh", self.source, self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rows(self.output, "SELECT _id FROM message"), [(1,), (2,)])
        self.assertEqual(
            rows(self.output, "SELECT message_row_id FROM message_media"), [(2,)]
        )
        self.assertEqual(
            rows(self.source, "SELECT * FROM chat"),
            rows(self.output, "SELECT * FROM chat"),
        )
        self.assertEqual(digest(self.source), before)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.directory.glob(".wa-*")), [])

    def test_dry_run_does_not_publish_or_change_source(self):
        before = digest(self.source)
        result = self.run_tool("wa-clean.sh", self.source, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dry run", result.stdout)
        self.assertEqual(digest(self.source), before)
        self.assertFalse(self.output.exists())

    def test_existing_output_is_preserved(self):
        self.output.write_bytes(b"previous successful result")
        before = self.output.read_bytes()
        result = self.run_tool("wa-clean.sh", self.source, self.output)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), before)

    def test_source_aliases_are_rejected(self):
        before = digest(self.source)
        for alias in (self.source, self.output):
            if alias == self.output:
                alias.symlink_to(self.source)
            result = self.run_tool("wa-clean.sh", self.source, alias)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(digest(self.source), before)

    def test_dangling_symlink_and_stale_wal_are_rejected(self):
        self.output.symlink_to(self.directory / "absent")
        result = self.run_tool("wa-clean.sh", self.source, self.output)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.output.is_symlink())
        self.output.unlink()
        sidecar = Path(str(self.output) + "-wal")
        sidecar.write_bytes(b"stale")
        result = self.run_tool("wa-clean.sh", self.source, self.output)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sidecar.read_bytes(), b"stale")

    def test_fixed_scratch_names_are_not_touched(self):
        renamed = self.directory / "cleanup.sql"
        self.source.rename(renamed)
        before = digest(renamed)
        result = self.run_tool("wa-clean.sh", renamed, self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(digest(renamed), before)

    def test_failure_during_delete_never_publishes_partial_output(self):
        self.assert_refused(
            "CREATE TRIGGER stop_delete BEFORE DELETE ON message BEGIN SELECT RAISE(ABORT, 'test failure'); END;",
            "test failure",
        )

    def test_affected_chat_pointer_is_refused(self):
        self.assert_refused(
            "INSERT INTO message VALUES (4,10,NULL,'bad-type'); UPDATE chat SET last_message_row_id=4;",
            "No safe replacement",
        )

    def test_existing_dangling_chat_pointer_is_refused(self):
        self.assert_refused(
            "UPDATE chat SET display_message_row_id=999;", "Database checks failed"
        )

    def test_integrity_failure_is_not_success(self):
        self.assert_refused(
            "CREATE TABLE checked(n INTEGER CHECK(n>0)); PRAGMA ignore_check_constraints=ON; INSERT INTO checked VALUES(-1);",
            "integrity check failed",
        )

    def test_foreign_key_failure_is_not_success(self):
        self.assert_refused(
            "CREATE TABLE dependent(chat_id REFERENCES chat(_id)); INSERT INTO dependent VALUES(999);",
            "Database checks failed",
        )

    def test_trigger_changes_to_chats_are_refused(self):
        self.assert_refused(
            "CREATE TRIGGER change_chat AFTER DELETE ON message BEGIN UPDATE chat SET last_message_row_id=2, display_message_row_id=0; END;",
            "trigger changed the chat",
        )

    def test_nulls_do_not_hide_bad_messages(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.executescript(
                "INSERT INTO message VALUES (4,NULL,0,'no-chat'), (5,99,0,NULL), (6,10,NULL,NULL), (7,99,0,'-1');"
            )
        result = self.run_tool("wa-clean.sh", self.source, self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rows(self.output, "SELECT _id FROM message"), [(1,), (2,)])

    def test_backup_includes_committed_wal(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("INSERT INTO message VALUES (7,10,0,'committed-wal')")
            connection.commit()
            self.assertTrue(Path(str(self.source) + "-wal").exists())
            result = self.run_tool("wa-clean.sh", self.source, self.output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                rows(self.output, "SELECT _id FROM message WHERE _id=7"), [(7,)]
            )

    def test_identifier_quoting_and_existing_auxiliary_table(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.executescript("""CREATE TABLE "odd'""table" (message_row_id INTEGER);
                INSERT INTO "odd'""table" VALUES(999);
                CREATE TABLE bad_msgs (value TEXT); INSERT INTO bad_msgs VALUES('keep');""")
        result = self.run_tool("wa-clean.sh", self.source, self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rows(self.output, "SELECT * FROM bad_msgs"), [("keep",)])
        self.assertEqual(
            rows(self.output, '''SELECT COUNT(*) FROM "odd'""table"'''), [(0,)]
        )

    def test_diagnose_missing_file_does_not_create_it(self):
        missing = self.directory / "missing.db"
        result = self.run_tool("wa-diagnose.sh", missing)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(missing.exists())

    def test_diagnose_reports_findings_and_healthy_result(self):
        before = digest(self.source)
        result = self.run_tool("wa-diagnose.sh", self.source)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["bad_messages"], 1)
        self.assertEqual(digest(self.source), before)
        self.assertEqual(
            self.run_tool("wa-clean.sh", self.source, self.output).returncode, 0
        )
        self.assertEqual(self.run_tool("wa-diagnose.sh", self.output).returncode, 0)

    def test_unsupported_schema_is_refused_before_publication(self):
        self.assert_refused(
            "ALTER TABLE message RENAME COLUMN message_type TO other_type;",
            "Unsupported schema",
        )

    def test_publish_does_not_overwrite_a_racing_file(self):
        staged = self.directory / "staged"
        staged.write_bytes(b"new")
        real_link = os.link

        def concurrent_link(source, destination):
            Path(destination).write_bytes(b"other process")
            return real_link(source, destination)

        with patch("wa_db.os.link", side_effect=concurrent_link):
            with self.assertRaises(FileExistsError):
                wa_db.publish(staged, self.output)
        self.assertEqual(self.output.read_bytes(), b"other process")


class RepackTests(DatabaseFixture):
    def setUp(self):
        super().setUp()
        from wa_crypt_tools.lib.db.db15 import Database15
        from wa_crypt_tools.lib.db.dbfactory import DatabaseFactory
        from wa_crypt_tools.lib.key.key15 import Key15
        from wa_crypt_tools.lib.props import Props

        self.factory = DatabaseFactory
        self.key = Key15(key=os.urandom(32))
        self.key_path = self.directory / "encrypted_backup.key"
        self.key_path.write_bytes(self.key.dump())
        self.reference = self.directory / "reference.crypt15"
        writer = Database15(iv=bytes(range(16)))
        props = Props()
        encoded = writer.encrypt(
            self.key, props, zlib.compress(self.source.read_bytes(), 9)
        )
        parsed = self.factory.from_file(io.BytesIO(encoded))
        # Unknown protobuf field 100 must survive the header copy too.
        parsed.prefix.MergeFromString(b"\xa0\x06\x07")
        writer.prefix = parsed.prefix
        self.reference.write_bytes(
            writer.encrypt(self.key, props, zlib.compress(self.source.read_bytes(), 9))
        )
        self.clean = self.directory / "roundtrip.db"
        result = self.run_tool("wa-clean.sh", self.source, self.clean)
        self.assertEqual(result.returncode, 0, result.stderr)

    def parse(self, path):
        with path.open("rb") as stream:
            db = self.factory.from_file(stream)
            compressed = db.decrypt(self.key, stream.read())
        return db, zlib.decompress(compressed)

    def test_repack_uses_fresh_iv_preserves_metadata_and_roundtrips(self):
        before = {
            path: digest(path) for path in (self.clean, self.reference, self.key_path)
        }
        expected = self.directory / "expected-snapshot.db"
        wa_db.snapshot(self.clean, expected)
        original, _ = self.parse(self.reference)
        ivs = {original.get_iv()}
        for index in range(2):
            output = self.directory / f"new-{index}.crypt15"
            result = self.run_tool(
                "wa-repack.sh", self.key_path, self.clean, self.reference, output
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            encrypted, recovered = self.parse(output)
            self.assertNotIn(encrypted.get_iv(), ivs)
            ivs.add(encrypted.get_iv())
            encrypted.prefix.e2ee_key_data.encryption_iv = original.get_iv()
            self.assertEqual(encrypted.prefix, original.prefix)
            # The backup API can normalize SQLite header counters; compare the exact
            # snapshot bytes, not the original file's bookkeeping bytes.
            self.assertEqual(recovered, expected.read_bytes())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual({path: digest(path) for path in before}, before)
        self.assertEqual(list(self.directory.glob(".wa-*")), [])

    def test_repack_rejects_output_aliases_and_existing_files(self):
        for output in (self.reference, self.clean, self.key_path):
            before = digest(output)
            result = self.run_tool(
                "wa-repack.sh", self.key_path, self.clean, self.reference, output
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(digest(output), before)

    def test_wrong_key_is_rejected_without_output(self):
        from wa_crypt_tools.lib.key.key15 import Key15

        self.key_path.write_bytes(Key15(key=os.urandom(32)).dump())
        result = self.run_tool(
            "wa-repack.sh", self.key_path, self.clean, self.reference, self.output
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_tampered_reference_is_rejected_without_output(self):
        data = bytearray(self.reference.read_bytes())
        data[-40] ^= 1
        self.reference.write_bytes(data)
        result = self.run_tool(
            "wa-repack.sh", self.key_path, self.clean, self.reference, self.output
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_unclean_database_is_not_repacked(self):
        result = self.run_tool(
            "wa-repack.sh", self.key_path, self.source, self.reference, self.output
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Database checks failed", result.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.directory.glob(".wa-*")), [])

    def test_failed_roundtrip_is_not_published(self):
        # Damage the newly written encrypted file as the verifier opens it.
        open_file = Path.open

        def corrupt_output(path, *args, **kwargs):
            if path.name == "backup.crypt15" and args == ("rb",):
                with open_file(path, "r+b") as stream:
                    stream.seek(-40, 2)
                    value = stream.read(1)
                    stream.seek(-1, 1)
                    stream.write(bytes([value[0] ^ 1]))
            return open_file(path, *args, **kwargs)

        with patch.object(Path, "open", corrupt_output):
            with self.assertRaises(wa_db.RecoveryError):
                wa_repack.repack(self.key_path, self.clean, self.reference, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.directory.glob(".wa-*")), [])


if __name__ == "__main__":
    unittest.main()
