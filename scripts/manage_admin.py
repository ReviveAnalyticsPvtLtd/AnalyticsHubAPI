"""
manage_admin.py

Operator CLI for administrator accounts.

Administrator lifecycle is deliberately CLI-only. There is no HTTP endpoint for
creating, disabling, or re-crediting an admin, so taking over an account
requires server access rather than a stolen admin token.

No subcommand accepts a password as an argument. Passwords are always read
through hidden prompts so they never reach shell history or process listings.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["buildParser", "main"]


import argparse
import getpass
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

# Nothing else in the codebase reads .env: the deployed app receives its
# configuration from the container environment. This CLI is run by hand from a
# developer machine, where those variables are only present in .env, so it loads
# the file itself. Existing environment variables win, so an operator can
# override a value for one invocation without editing the file.
from dotenv import load_dotenv

load_dotenv(REPOSITORY_ROOT / ".env", override=False)

from api.adminErrors import AdminApiError
from api.services.adminAuthService import getAdminAuthService


PASSWORD_MISMATCH_EXIT = 2


def buildParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage administrator accounts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="Create an administrator account.")
    add.add_argument("--email", required=True)
    add.add_argument("--name", required=True)

    subparsers.add_parser("list", help="List administrator accounts.")

    deactivate = subparsers.add_parser(
        "deactivate",
        help="Disable an administrator and revoke their live sessions.",
    )
    deactivate.add_argument("--email", required=True)

    activate = subparsers.add_parser(
        "activate", help="Re-enable a disabled administrator."
    )
    activate.add_argument("--email", required=True)

    resetPassword = subparsers.add_parser(
        "reset-password",
        help="Replace an administrator password and revoke their live sessions.",
    )
    resetPassword.add_argument("--email", required=True)

    return parser


def _promptForPassword() -> str | None:
    """
    Read a password twice through hidden prompts.

    Returns:
        str | None: The password, or None when the two entries differ.
    """
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        return None
    return password


def _commandAdd(args, service) -> int:
    password = _promptForPassword()
    if password is None:
        print("Passwords do not match.", file=sys.stderr)
        return PASSWORD_MISMATCH_EXIT
    admin = service.createAdmin(args.email, args.name, password)
    print(f"Created admin {admin['id']} ({admin['email']})")
    return 0


def _commandList(_args, service) -> int:
    admins = service.listAdmins()
    if not admins:
        print("No administrators found.")
        return 0
    print(f"{'EMAIL':<40} {'NAME':<24} {'ACTIVE':<7} LAST LOGIN")
    for admin in admins:
        lastLogin = admin.get("last_login_at") or "never"
        active = "yes" if admin.get("is_active") else "no"
        print(
            f"{str(admin.get('email')):<40} {str(admin.get('name')):<24} "
            f"{active:<7} {lastLogin}"
        )
    return 0


def _commandDeactivate(args, service) -> int:
    result = service.setAdminActive(args.email, False)
    print(
        f"Deactivated {result['email']}; "
        f"revoked {result['revokedSessions']} session(s)."
    )
    return 0


def _commandActivate(args, service) -> int:
    result = service.setAdminActive(args.email, True)
    print(f"Activated {result['email']}.")
    return 0


def _commandResetPassword(args, service) -> int:
    password = _promptForPassword()
    if password is None:
        print("Passwords do not match.", file=sys.stderr)
        return PASSWORD_MISMATCH_EXIT
    result = service.changeAdminPassword(args.email, password)
    print(
        f"Password updated for {result['email']}; "
        f"revoked {result['revokedSessions']} session(s)."
    )
    return 0


COMMANDS = {
    "add": _commandAdd,
    "list": _commandList,
    "deactivate": _commandDeactivate,
    "activate": _commandActivate,
    "reset-password": _commandResetPassword,
}


def main(argv=None) -> int:
    parser = buildParser()
    args = parser.parse_args(argv)
    handler = COMMANDS[args.command]
    try:
        return handler(args, getAdminAuthService())
    except AdminApiError as exc:
        print(exc.message, file=sys.stderr)
        for field, message in (exc.errors or {}).items():
            print(f"  {field}: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
