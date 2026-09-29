"""Conservative checks and cleanup for a decrypted WhatsApp msgstore database."""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path


class RecoveryError(Exception):
    pass


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


def input_file(value):
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file():
        raise RecoveryError(f"Not a regular file: {path}")
    return path


def output_file(value):
    # Do not resolve the last component: even a dangling symlink is an existing output.
    path = Path(os.path.abspath(Path(value).expanduser()))
    if os.path.lexists(path):
        raise RecoveryError(f"Output already exists; choose a new filename: {path}")
    if not path.parent.is_dir():
        raise RecoveryError(f"Output directory does not exist: {path.parent}")
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(str(path) + suffix):
            raise RecoveryError(f"SQLite sidecar already exists: {path}{suffix}")
    return path


def publish(staged, destination):
    """Publish a complete file without replacing an existing file or symlink."""
    os.chmod(staged, 0o600)
    with open(staged, "rb") as stream:
        os.fsync(stream.fileno())
    # Staging is in the destination directory, so this is an atomic, same-filesystem
    # operation. Unlike replace(), link() fails if someone created the output meanwhile.
    output_file(destination)
    os.link(staged, destination)


def open_readonly(path):
    connection = sqlite3.connect(
        input_file(path).as_uri() + "?mode=ro", uri=True, isolation_level=None
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


def snapshot(source, destination):
    # SQLite's backup API includes committed WAL contents and takes a consistent snapshot.
    with (
        closing(open_readonly(source)) as reader,
        closing(sqlite3.connect(destination)) as writer,
    ):
        reader.backup(writer)
        writer.execute("PRAGMA journal_mode=DELETE")
    os.chmod(destination, 0o600)


def columns(connection, table):
    return connection.execute(f"PRAGMA table_info({identifier(table)})").fetchall()


def schema(connection):
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    required = {
        "message": {"_id", "chat_row_id", "message_type", "key_id"},
        "chat": {"_id"},
        "message_media": {"message_row_id"},
    }
    for table, names in required.items():
        info = columns(connection, table)
        if table not in tables or not names.issubset({row[1] for row in info}):
            raise RecoveryError(
                f"Unsupported schema: {table} requires {', '.join(sorted(names))}"
            )
        if table in ("message", "chat"):
            primary_key = [(row[1], row[2].upper()) for row in info if row[5]]
            if primary_key != [("_id", "INTEGER")]:
                raise RecoveryError(
                    f"Unsupported schema: {table}._id must be an INTEGER PRIMARY KEY"
                )
    children = {}
    for table in sorted(tables - {"chat", "message"}):
        refs = [
            row[1]
            for row in columns(connection, table)
            if row[1] in ("message_row_id", "parent_message_row_id")
        ]
        if refs:
            children[table] = refs
    chat_refs = [
        row[1]
        for row in columns(connection, "chat")
        if row[1] == "message_row_id" or row[1].endswith("_message_row_id")
    ]
    return children, chat_refs


# Preserve only the documented sentinel. IS comparisons deliberately handle NULLs.
BAD_MESSAGE = """
NOT (m._id = 1 AND m.chat_row_id IS -1 AND m.key_id IS '-1' AND m.message_type IS NULL)
AND (m.message_type IS NULL OR NOT EXISTS (SELECT 1 FROM chat c WHERE c._id = m.chat_row_id))
"""


def scalar(connection, query):
    return connection.execute(query).fetchone()[0]


def orphan_condition(refs):
    return " OR ".join(
        f"({identifier(ref)} > 0 AND {identifier(ref)} NOT IN (SELECT _id FROM message))"
        for ref in refs
    )


def report(connection):
    children, chat_refs = schema(connection)
    return {
        "integrity": [row[0] for row in connection.execute("PRAGMA integrity_check")],
        "foreign_key_errors": len(
            connection.execute("PRAGMA foreign_key_check").fetchall()
        ),
        "messages": scalar(connection, "SELECT COUNT(*) FROM message"),
        "chats": scalar(connection, "SELECT COUNT(*) FROM chat"),
        "bad_messages": scalar(
            connection, f"SELECT COUNT(*) FROM message m WHERE {BAD_MESSAGE}"
        ),
        "orphan_children": {
            table: scalar(
                connection,
                f"SELECT COUNT(*) FROM {identifier(table)} WHERE {orphan_condition(refs)}",
            )
            for table, refs in children.items()
        },
        "dangling_chat_pointers": {
            ref: scalar(
                connection, f"SELECT COUNT(*) FROM chat WHERE {orphan_condition([ref])}"
            )
            for ref in chat_refs
        },
    }


def healthy(result):
    return (
        result["integrity"] == ["ok"]
        and result["foreign_key_errors"] == 0
        and result["bad_messages"] == 0
        and not any(result["orphan_children"].values())
        and not any(result["dangling_chat_pointers"].values())
    )


def require_integrity(connection):
    rows = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if rows != ["ok"]:
        raise RecoveryError("SQLite integrity check failed: " + "; ".join(rows[:5]))


def require_healthy(connection):
    result = report(connection)
    if not healthy(result):
        raise RecoveryError("Database checks failed: " + json.dumps(result))
    return result


def diagnose(source):
    with closing(open_readonly(source)) as connection:
        connection.execute("BEGIN")
        result = report(connection)
        print(json.dumps(result, indent=2))
        return 0 if healthy(result) else 1


def clean(source, destination=None, dry_run=False):
    source = input_file(source)
    if destination is None and not dry_run:
        raise RecoveryError("An output filename is required unless --dry-run is used")
    destination = output_file(destination) if destination is not None else None
    with tempfile.TemporaryDirectory(
        prefix=".wa-clean-", dir=destination.parent if destination else None
    ) as directory:
        staged = Path(directory) / "clean.db"
        snapshot(source, staged)
        with closing(sqlite3.connect(staged, isolation_level=None)) as connection:
            require_integrity(connection)
            children, chat_refs = schema(connection)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                before = scalar(connection, "SELECT COUNT(*) FROM message")
                chats_before = connection.execute(
                    "SELECT * FROM chat ORDER BY _id"
                ).fetchall()
                connection.execute(
                    "CREATE TEMP TABLE wa_bad_messages (_id INTEGER PRIMARY KEY)"
                )
                connection.execute(
                    f"INSERT INTO wa_bad_messages SELECT m._id FROM message m WHERE {BAD_MESSAGE}"
                )
                bad = scalar(connection, "SELECT COUNT(*) FROM temp.wa_bad_messages")
                for ref in chat_refs:
                    count = scalar(
                        connection,
                        f"SELECT COUNT(*) FROM chat WHERE {identifier(ref)} IN (SELECT _id FROM temp.wa_bad_messages)",
                    )
                    if count:
                        raise RecoveryError(
                            f"Refusing cleanup: {count} chat row(s) reference a selected message through {ref}. No safe replacement is known."
                        )
                for table, refs in children.items():
                    condition = " OR ".join(
                        f"{identifier(ref)} IN (SELECT _id FROM temp.wa_bad_messages)"
                        for ref in refs
                    )
                    connection.execute(
                        f"DELETE FROM {identifier(table)} WHERE {condition}"
                    )
                connection.execute(
                    "DELETE FROM message WHERE _id IN (SELECT _id FROM temp.wa_bad_messages)"
                )
                for table, refs in children.items():
                    connection.execute(
                        f"DELETE FROM {identifier(table)} WHERE {orphan_condition(refs)}"
                    )
                result = require_healthy(connection)
                if result["messages"] != before - bad:
                    raise RecoveryError(
                        "Unexpected message count after cleanup; refusing output"
                    )
                if (
                    chats_before
                    != connection.execute("SELECT * FROM chat ORDER BY _id").fetchall()
                ):
                    raise RecoveryError(
                        "A trigger changed the chat table; refusing output"
                    )
                if dry_run:
                    connection.execute("ROLLBACK")
                    print(
                        f"Dry run: {bad} message(s) would be deleted; all checks passed on a temporary copy."
                    )
                    return
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            connection.execute("VACUUM")
            require_healthy(connection)
        publish(staged, destination)
    print(f"Messages: {before} -> {result['messages']} (deleted: {bad})")
    print(f"All database checks passed. Created: {destination}")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("diagnose").add_argument("source")
    cleanup = commands.add_parser("clean")
    cleanup.add_argument("source")
    cleanup.add_argument("destination", nargs="?")
    cleanup.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "diagnose":
            return diagnose(args.source)
        clean(args.source, args.destination, args.dry_run)
        return 0
    except (RecoveryError, OSError, sqlite3.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
