"""Security regression tests for BOBO's opt-in local web remote.

AST isolation keeps these tests runnable on Linux CI without importing the
Windows-only GUI/audio dependencies or opening a listening socket.
"""
import ast
import hmac
import hashlib
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
from unittest import mock
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "app.py"


def load_security_functions(directory, bind="127.0.0.1", password=""):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    selected = []
    names = {"load_web_password", "_session_secret", "session_cookie_value", "start_web_server"}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            selected.append(node)
    assert len(selected) == len(names)
    class FakeServer:
        calls = []
        def __init__(self, address, handler):
            self.calls.append(address)
        def serve_forever(self):
            pass
    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
        def start(self):
            pass

    namespace = {
        "BASE_DIR": directory,
        "os": os,
        "secrets": secrets,
        "threading": SimpleNamespace(Lock=threading.Lock, Thread=FakeThread),
        "hmac": hmac,
        "hashlib": hashlib,
        "WEB_BIND": bind,
        "WEB_PORT": 49731,
        "WEB_PASSWORD": password,
        "WEB_SESSION_DAYS": 1,
        "SCREEN_VIEW": False,
        "_session_key_lock": threading.Lock(),
        "_session_key_cache": None,
        "ThreadingHTTPServer": FakeServer,
        "RemoteHandler": object,
        "lan_ip": lambda: "192.168.1.123",
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])),
                 str(SOURCE), "exec"), namespace)
    return namespace, FakeServer


class RemoteSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name

    def test_secure_defaults_are_explicit(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                if isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    if name in {"WEB_BIND", "SCREEN_VIEW", "WEB_PASSWORD", "WEB_SESSION_DAYS"}:
                        values[name] = ast.literal_eval(node.value)
        self.assertEqual(values, {
            "WEB_BIND": "127.0.0.1",
            "SCREEN_VIEW": False,
            "WEB_PASSWORD": "",
            "WEB_SESSION_DAYS": 1,
        })

    def test_no_password_prevents_server_start(self):
        ns, server = load_security_functions(self.folder)
        self.assertIsNone(ns["start_web_server"]())
        self.assertEqual(server.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.folder, ".bobo_session_key")))

    def test_short_password_prevents_server_start(self):
        ns, server = load_security_functions(self.folder, password="short")
        self.assertIsNone(ns["start_web_server"]())
        self.assertEqual(server.calls, [])

    def test_local_password_file_enables_loopback_only(self):
        with open(os.path.join(self.folder, "web_password.txt"), "w") as f:
            f.write("this-password-is-long-enough\n")
        ns, server = load_security_functions(self.folder)
        self.assertIsNotNone(ns["start_web_server"]())
        self.assertEqual(server.calls, [("127.0.0.1", 49731)])
        self.assertEqual(len(ns["_session_secret"]()), 64)

    def test_password_explicitly_required_for_lan(self):
        ns, server = load_security_functions(self.folder, bind="0.0.0.0")
        self.assertIsNone(ns["start_web_server"]())
        self.assertEqual(server.calls, [])
        ns["WEB_PASSWORD"] = "strong-lan-password"
        self.assertIsNotNone(ns["start_web_server"]())
        self.assertEqual(server.calls, [("0.0.0.0", 49731)])

    def test_secret_is_random_and_persisted_across_restarts(self):
        ns1, _ = load_security_functions(self.folder)
        first = ns1["_session_secret"]()
        ns2, _ = load_security_functions(self.folder)
        second = ns2["_session_secret"]()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, "bobo-fallback-key")
        if os.name != "nt":
            self.assertEqual(os.stat(os.path.join(self.folder, ".bobo_session_key")).st_mode & 0o777, 0o600)

    def test_missing_key_storage_fails_closed(self):
        ns, server = load_security_functions(self.folder, password="strong-password-123")
        with mock.patch.object(os, "open", side_effect=PermissionError("denied")):
            self.assertIsNone(ns["start_web_server"]())
        self.assertEqual(server.calls, [])

    def test_invalid_existing_key_blocks_remote(self):
        with open(os.path.join(self.folder, ".bobo_session_key"), "w") as f:
            f.write("predictable-invalid-key")
        ns, server = load_security_functions(self.folder, password="strong-password-123")
        with self.assertRaises(ValueError):
            ns["_session_secret"]()
        self.assertIsNone(ns["start_web_server"]())
        self.assertEqual(server.calls, [])

    def test_session_cookie_value_changes_with_password(self):
        ns, _ = load_security_functions(self.folder, password="password-number-one")
        a = ns["session_cookie_value"]()
        ns["WEB_PASSWORD"] = "password-number-two"
        b = ns["session_cookie_value"]()
        self.assertNotEqual(a, b)

    def test_cannot_authorize_when_no_password(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        handler = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RemoteHandler")
        method = next(n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name == "_authorized")
        ns = {"load_web_password": lambda: "", "hmac": hmac, "session_cookie_value": lambda: "anything"}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     str(SOURCE), "exec"), ns)
        self.assertFalse(ns["_authorized"](SimpleNamespace(headers={"Cookie": "bobo=anything"})))

    def test_source_has_no_predictable_fallback_or_lax_cookie(self):
        code = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn('return "bobo-fallback-key"', code)
        self.assertIn("SameSite=Strict", code)
        self.assertNotIn("SameSite=Lax", code)

    def test_full_session_login_not_required_for_offline_voice(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        names = [n.func.id for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertIn("start_web_server", names)
        self.assertIn("Listener", names)  # offline assistant still initializes


if __name__ == "__main__":
    unittest.main()
