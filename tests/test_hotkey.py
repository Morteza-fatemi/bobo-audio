"""Platform-independent regression tests for the Windows Ctrl+Shift+B hook.

Only the watcher class is compiled from the source AST. No microphone,
Windows API, Vosk model or real hook is required to run these tests.
"""
import ast
import ctypes
from pathlib import Path
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "app.py"


class KeyEvent(ctypes.Structure):
    _fields_ = [("vkCode", ctypes.c_uint32)]


class FakeUser32:
    def __init__(self):
        self.pressed = set()
        self.SetWindowsHookExW = Mock(return_value=12345)
        self.CallNextHookEx = Mock(return_value=99)
        self.GetAsyncKeyState = Mock(side_effect=self.key_state)
        self.UnhookWindowsHookEx = Mock(return_value=1)
        self.GetMessageW = Mock(return_value=0)
        self.TranslateMessage = Mock()
        self.DispatchMessageW = Mock()

    def key_state(self, vk):
        return -32768 if vk in self.pressed else 0


def load_watcher(user32, wake):
    """Load the real watcher method bodies with mocked Win32 dependencies."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    matches = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GlobalKeyWatcher"
    ]
    assert len(matches) == 1, "Expected one GlobalKeyWatcher class"
    klass = matches[0]
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "run"
        for node in klass.body
    ), "GlobalKeyWatcher.run must be a class method, not a top-level function"
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "run"
        for node in tree.body
    ), "Unexpected top-level run() function"

    win_ctypes = SimpleNamespace(
        windll=SimpleNamespace(user32=user32),
        cast=ctypes.cast,
        POINTER=ctypes.POINTER,
        byref=ctypes.byref,
        c_int=ctypes.c_int,
        c_void_p=ctypes.c_void_p,
        c_uint=ctypes.c_uint,
        c_size_t=ctypes.c_size_t,
        c_ssize_t=ctypes.c_ssize_t,
        c_short=ctypes.c_short,
    )
    namespace = {
        "threading": threading,
        "ctypes": win_ctypes,
        "wintypes": SimpleNamespace(MSG=ctypes.c_int),
        "_KBDLLHOOKSTRUCT": KeyEvent,
        "_LowLevelKeyboardProc": lambda callback: callback,
        "VK_B": 0x42,
        "manual_wake": wake,
    }
    module = ast.fix_missing_locations(ast.Module(body=[klass], type_ignores=[]))
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["GlobalKeyWatcher"]


class GlobalHotkeyTests(unittest.TestCase):
    def setUp(self):
        self.user32 = FakeUser32()
        self.wake = threading.Event()
        self.messages = queue.Queue()
        self.watcher = load_watcher(self.user32, self.wake)(self.messages)

    def send(self, vk, message=0x0100, ncode=0):
        event = KeyEvent(vk)
        return self.watcher._callback(ncode, message, ctypes.addressof(event))

    def test_thread_start_installs_hook_and_cleans_up(self):
        self.watcher.start()
        self.watcher.join(timeout=2)
        self.assertFalse(self.watcher.is_alive())
        args = self.user32.SetWindowsHookExW.call_args.args
        self.assertEqual(args[0], 13)  # WH_KEYBOARD_LL
        self.assertTrue(callable(args[1]))
        self.user32.UnhookWindowsHookEx.assert_called_once_with(12345)

    def test_failed_install_does_not_unhook_invalid_handle(self):
        self.user32.SetWindowsHookExW.return_value = None
        self.watcher.run()
        self.user32.UnhookWindowsHookEx.assert_not_called()

    def test_ctrl_shift_b_triggers_wake_and_overlay(self):
        self.user32.pressed.update((0x11, 0x10))  # generic Ctrl and Shift
        self.assertEqual(self.send(0x42), 99)
        self.assertTrue(self.wake.is_set())
        self.assertEqual(self.messages.get_nowait(), "show")
        self.user32.CallNextHookEx.assert_called_once()

    def test_b_without_both_modifiers_does_not_trigger(self):
        for pressed in (set(), {0x11}, {0x10}):
            with self.subTest(pressed=pressed):
                self.user32.pressed = pressed
                self.send(0x42)
                self.assertFalse(self.wake.is_set())
                self.assertTrue(self.messages.empty())
                self.send(0x42, 0x0101)  # B key released

    def test_auto_repeat_is_suppressed_until_release(self):
        self.user32.pressed = {0x11, 0x10}
        for _ in range(5):
            self.send(0x42)
        self.assertEqual(self.messages.qsize(), 1)
        self.send(0x42, 0x0101)
        self.send(0x42)
        self.assertEqual(self.messages.qsize(), 2)

    def test_syskey_down_up_paths(self):
        self.user32.pressed = {0x11, 0x10}
        self.send(0x42, 0x0104)  # WM_SYSKEYDOWN
        self.send(0x42, 0x0105)  # WM_SYSKEYUP
        self.send(0x42, 0x0100)
        self.assertEqual(self.messages.qsize(), 2)

    def test_negative_hook_code_and_unrelated_keys(self):
        self.user32.pressed = {0x11, 0x10}
        self.send(0x42, ncode=-1)
        self.send(0x41)  # A, not B
        self.assertTrue(self.messages.empty())
        self.assertEqual(self.user32.CallNextHookEx.call_count, 2)

    def test_message_loop_failure_still_unhooks(self):
        self.user32.GetMessageW.return_value = -1
        self.watcher.run()
        self.user32.UnhookWindowsHookEx.assert_called_once_with(12345)


if __name__ == "__main__":
    unittest.main()
