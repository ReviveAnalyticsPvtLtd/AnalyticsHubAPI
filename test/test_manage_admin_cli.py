import unittest
import sys
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.adminErrors import AdminApiError
from api.services.adminAuthService import AdminAuthService
from scripts import manage_admin


class FakeAdminTable:
    def __init__(self, adminUsers, insertError=None):
        self.adminUsers = adminUsers
        self.insertError = insertError
        self.email = None
        self.insertedRows = []
        self.operation = None

    def select(self, _fields):
        self.operation = "select"
        return self

    def eq(self, _field, email):
        self.email = email
        return self

    def limit(self, _count):
        return self

    def insert(self, row):
        self.operation = "insert"
        self.insertedRows.append(row)
        return self

    def execute(self):
        if self.operation == "insert":
            if self.insertError:
                raise self.insertError
            row = {"id": f"admin-{len(self.adminUsers) + 1}", **self.insertedRows[-1]}
            self.adminUsers.append(row)
            return SimpleNamespace(data=[row])
        return SimpleNamespace(data=[row for row in self.adminUsers if row["email"] == self.email][:1])


class FakeAdminClient:
    def __init__(self, adminUsers, insertError=None):
        self.adminUsers = FakeAdminTable(adminUsers, insertError)

    def table(self, name):
        if name != "admin_users":
            raise AssertionError(f"Unexpected table: {name}")
        return self.adminUsers


def buildFakeAdminClient(adminUsers, insertError=None):
    return FakeAdminClient(adminUsers, insertError)


class AdminProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.client = buildFakeAdminClient(adminUsers=[])
        self.hasher = Mock()
        self.hasher.hash.return_value = "$argon2id$test-hash"
        self.service = AdminAuthService(client=self.client, passwordHasher=self.hasher)

    def test_create_admin_normalizes_and_hashes(self):
        created = self.service.createAdmin(" Admin@Example.COM ", " Admin Name ", "correct horse battery")
        self.assertEqual("admin@example.com", created["email"])
        self.assertEqual("Admin Name", created["name"])
        self.assertNotIn("password_hash", created)
        self.hasher.hash.assert_called_once_with("correct horse battery")
        inserted = self.client.adminUsers.insertedRows[0]
        self.assertEqual("$argon2id$test-hash", inserted["password_hash"])
        self.assertNotIn("correct horse battery", inserted.values())

    def test_create_admin_rejects_duplicate_and_weak_password(self):
        self.service.createAdmin("admin@example.com", "One", "correct horse battery")
        with self.assertRaises(AdminApiError) as duplicate:
            self.service.createAdmin("ADMIN@example.com", "Two", "another correct password")
        self.assertEqual(409, duplicate.exception.statusCode)
        with self.assertRaises(AdminApiError) as weak:
            self.service.createAdmin("two@example.com", "Two", "short")
        self.assertEqual(422, weak.exception.statusCode)

    def test_create_admin_maps_concurrent_duplicate_insert_to_conflict(self):
        client = buildFakeAdminClient([], insertError=RuntimeError("SQLSTATE 23505 duplicate key"))
        service = AdminAuthService(client=client, passwordHasher=self.hasher)
        with self.assertRaises(AdminApiError) as duplicate:
            service.createAdmin("admin@example.com", "Admin", "correct horse battery")
        self.assertEqual(409, duplicate.exception.statusCode)

    @patch("scripts.manage_admin.getpass.getpass", side_effect=["correct horse battery", "correct horse battery"])
    @patch("scripts.manage_admin.getAdminAuthService")
    def test_cli_add_reads_hidden_confirmation(self, getService, _getpass):
        getService.return_value.createAdmin.return_value = {
            "id": "admin-id", "email": "admin@example.com", "name": "Admin Name"
        }
        exitCode = manage_admin.main(["add", "--email", "admin@example.com", "--name", "Admin Name"])
        self.assertEqual(0, exitCode)
        getService.return_value.createAdmin.assert_called_once()

    def test_cli_parser_does_not_accept_password_argument(self):
        with self.assertRaises(SystemExit):
            manage_admin.main(["add", "--email", "admin@example.com", "--name", "Admin Name", "--password", "secret"])

    def test_cli_help_runs_as_a_script(self):
        result = subprocess.run(
            [sys.executable, "scripts/manage_admin.py", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("--password", result.stdout)


if __name__ == "__main__":
    unittest.main()
