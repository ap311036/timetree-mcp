import subprocess
import unittest

from timetree_mcp.credentials import MacOSKeychainCredentialStore


class FakeKeychain:
    def __init__(self):
        self.items = {}
        self.calls = []

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        command = args[1]
        service = args[args.index("-s") + 1]
        if command == "find-generic-password":
            value = self.items.get(service)
            if value is None:
                return subprocess.CompletedProcess(args, 44, "", "could not be found")
            return subprocess.CompletedProcess(args, 0, value + "\n", "")
        if command == "add-generic-password":
            self.items[service] = args[args.index("-w") + 1]
            return subprocess.CompletedProcess(args, 0, "", "")
        if command == "delete-generic-password":
            self.items.pop(service, None)
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)


class CredentialStoreTests(unittest.TestCase):
    def test_reads_email_and_password_without_printing_values(self):
        fake = FakeKeychain()
        store = MacOSKeychainCredentialStore(runner=fake.run)
        store.write("me@example.com", "super-secret")

        credentials = store.read()

        self.assertEqual(credentials, ("me@example.com", "super-secret"))

    def test_status_reports_presence_only_and_clear_removes_items(self):
        fake = FakeKeychain()
        store = MacOSKeychainCredentialStore(runner=fake.run)
        store.write("me@example.com", "super-secret")

        status = store.status()
        store.clear()
        after = store.status()

        self.assertEqual(status, {"available": True, "email_configured": True, "password_configured": True})
        self.assertEqual(after, {"available": True, "email_configured": False, "password_configured": False})


if __name__ == "__main__":
    unittest.main()
