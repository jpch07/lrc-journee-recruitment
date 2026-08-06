import argparse
from getpass import getpass
from pathlib import Path

from argon2 import PasswordHasher


parser = argparse.ArgumentParser(description="Generate an Argon2 admin password hash.")
parser.add_argument("--output", type=Path, help="Write the hash to a local file instead of stdout.")
args = parser.parse_args()
password = getpass("Shared admin password: ")
confirmation = getpass("Confirm password: ")
if password != confirmation:
    raise SystemExit("Passwords do not match.")
if len(password) < 10:
    raise SystemExit("Use at least 10 characters.")
password_hash = PasswordHasher().hash(password)
if args.output:
    args.output.write_text(password_hash, encoding="utf-8")
    print("Password hash saved securely for deployment.")
else:
    print(password_hash)
