from getpass import getpass

from argon2 import PasswordHasher


password = getpass("Shared admin password: ")
confirmation = getpass("Confirm password: ")
if password != confirmation:
    raise SystemExit("Passwords do not match.")
if len(password) < 10:
    raise SystemExit("Use at least 10 characters.")
print(PasswordHasher().hash(password))

