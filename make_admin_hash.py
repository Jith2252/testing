"""Print an scrypt ADMIN_PASSWORD_HASH for deployment."""

from __future__ import annotations

import argparse
import getpass

from auth import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("password", nargs="?", help="password (omit to prompt securely)")
    args = parser.parse_args()
    password = args.password if args.password is not None else getpass.getpass("Admin password: ")
    print(hash_password(password))


if __name__ == "__main__":
    main()
