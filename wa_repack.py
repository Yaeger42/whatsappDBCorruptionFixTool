"""Repack a checked msgstore snapshot with a fresh crypt15 IV."""

import argparse
import hashlib
import os
import sqlite3
import sys
import tempfile
import zlib
from contextlib import closing
from pathlib import Path

from wa_db import (
    RecoveryError,
    input_file,
    open_readonly,
    output_file,
    publish,
    require_healthy,
    snapshot,
)


def repack(key_path, plain_path, reference_path, destination, allow_preexisting=False):
    # These APIs are tied to the revision in requirements.txt. The CLI's --reference
    # option reuses the reference IV, even if --iv is also supplied.
    from wa_crypt_tools.lib.constants import C
    from wa_crypt_tools.lib.db.db15 import Database15
    from wa_crypt_tools.lib.db.dbfactory import DatabaseFactory
    from wa_crypt_tools.lib.errors import WaCryptError
    from wa_crypt_tools.lib.key.key15 import Key15
    from wa_crypt_tools.lib.key.keyfactory import KeyFactory

    key_path, plain_path, reference_path = map(
        input_file, (key_path, plain_path, reference_path)
    )
    destination = output_file(destination)
    try:
        key = KeyFactory.new(str(key_path))
        if not isinstance(key, Key15):
            raise RecoveryError("A crypt15 encrypted_backup.key file is required")
        with reference_path.open("rb") as stream:
            reference = DatabaseFactory.from_file(stream)
            if not isinstance(reference, Database15):
                raise RecoveryError("The reference must be a crypt15 backup")
            # Authenticate the reference before trusting it as a template.
            compressed_reference = reference.decrypt(key, stream.read())
        level = C.ZLIB_HEADER_LEVELS.get(compressed_reference[:2])
        del compressed_reference
        if level is None:
            raise RecoveryError(
                "The reference is not a supported zlib-compressed msgstore backup"
            )

        with tempfile.TemporaryDirectory(
            prefix=".wa-repack-", dir=destination.parent
        ) as directory:
            plain = Path(directory) / "snapshot.db"
            encrypted = Path(directory) / "backup.crypt15"
            snapshot(plain_path, plain)
            with closing(open_readonly(plain)) as connection:
                require_healthy(connection, allow_preexisting)
            data = plain.read_bytes()
            size = len(data)
            digest = hashlib.sha256(data).digest()
            iv = os.urandom(16)
            while iv == reference.get_iv():
                iv = os.urandom(16)
            writer = Database15(iv=iv)
            writer.prefix = reference.prefix
            writer.feature_table = reference.feature_table
            encrypted.write_bytes(
                writer.encrypt(key, reference.props, zlib.compress(data, level))
            )
            del data

            with encrypted.open("rb") as stream:
                result = DatabaseFactory.from_file(stream)
                if not isinstance(result, Database15) or result.get_iv() != iv:
                    raise RecoveryError(
                        "Output crypt15 format or IV verification failed"
                    )
                header = type(result.prefix)()
                header.CopyFrom(result.prefix)
                header.e2ee_key_data.encryption_iv = reference.get_iv()
                if header != reference.prefix:
                    raise RecoveryError("Output metadata differs from the reference")
                compressed = result.decrypt(key, stream.read())
            decompressor = zlib.decompressobj()
            roundtrip = decompressor.decompress(compressed, size + 1)
            if (
                not decompressor.eof
                or decompressor.unused_data
                or decompressor.unconsumed_tail
                or len(roundtrip) != size
                or hashlib.sha256(roundtrip).digest() != digest
            ):
                raise RecoveryError("Round-trip verification failed")
            publish(encrypted, destination)
    except WaCryptError as error:
        raise RecoveryError(f"Crypt15 verification failed: {error}") from error
    print(
        "Verified: fresh IV, preserved header metadata, and matching decrypted snapshot."
    )
    print(f"Created: {destination}")
    print(
        "This verifies the file locally; acceptance by WhatsApp still requires a restore test."
    )


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key")
    parser.add_argument("plain")
    parser.add_argument("reference")
    parser.add_argument("output")
    parser.add_argument(
        "--allow-preexisting",
        action="store_true",
        help="Accept the same unrepaired findings that wa-clean.sh accepted.",
    )
    args = parser.parse_args()
    try:
        repack(
            args.key,
            args.plain,
            args.reference,
            args.output,
            args.allow_preexisting,
        )
        return 0
    except ImportError:
        print(
            "ERROR: Install the tested dependency with python3 -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    except (RecoveryError, OSError, sqlite3.Error, ValueError, zlib.error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
