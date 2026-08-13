import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.adminErrors import AdminApiError
from api.services.adminAuthService import getAdminAuthService


def buildParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage administrator accounts.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add = subparsers.add_parser("add", help="Create an administrator account.")
    add.add_argument("--email", required=True)
    add.add_argument("--name", required=True)
    return parser


def main(argv=None) -> int:
    parser = buildParser()
    args = parser.parse_args(argv)
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    try:
        admin = getAdminAuthService().createAdmin(args.email, args.name, password)
    except AdminApiError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print(f"Created admin {admin['id']} ({admin['email']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
