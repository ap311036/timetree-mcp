import subprocess
import unittest

from timetree_mcp.credentials import KeychainUnavailable, MacOSKeychainCredentialStore


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
            output = value + "\n" if "-w" in args else 'keychain item metadata\n'
            return subprocess.CompletedProcess(args, 0, output, "")
        if command == "add-generic-password":
            self.items[service] = args[args.index("-w") + 1]
            return subprocess.CompletedProcess(args, 0, "", "")
        if command == "delete-generic-password":
            self.items.pop(service, None)
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)


class CredentialStoreTests(unittest.TestCase):
    def test_status_queries_item_metadata_without_requesting_password_data(self):
        fake = FakeKeychain()
        store = MacOSKeychainCredentialStore(runner=fake.run)
        store.write("metadata@example.invalid", "synthetic-password")
        fake.calls.clear()

        self.assertEqual(store.status(), {
            "available": True, "email_configured": True, "password_configured": True,
        })

        self.assertEqual(len(fake.calls), 2)
        for args, _ in fake.calls:
            self.assertEqual(args[1], "find-generic-password")
            self.assertNotIn("-w", args)
            self.assertNotIn("-g", args)

    def test_status_reports_unavailable_without_attempting_another_backend(self):
        def unavailable(*args, **kwargs):
            raise KeychainUnavailable("synthetic unavailable")

        self.assertEqual(MacOSKeychainCredentialStore(runner=unavailable).status(), {
            "available": False, "email_configured": False, "password_configured": False,
        })

    def test_status_permission_failure_does_not_expose_command_output(self):
        def denied(args, **kwargs):
            self.assertNotIn("-w", args)
            self.assertNotIn("-g", args)
            return subprocess.CompletedProcess(args, 1, "synthetic private output", "denied")

        with self.assertRaisesRegex(RuntimeError, "^macOS Keychain status failed$"):
            MacOSKeychainCredentialStore(runner=denied).status()

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
