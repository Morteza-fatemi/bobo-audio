"""Windows-native API smoke checks.

Runs on windows-latest in CI. Tests genuine Win32 API availability and hook
registration where the runner allows it; does NOT emulate a human keyboard,
desktop lock screen, microphone, audio stack, or live Windows UI automation.
"""
import ctypes
import os
import sys
import unittest
from ctypes import wintypes


@unittest.skipUnless(sys.platform == "win32", "requires Windows")
class NativeWindowsHookSmoke(unittest.TestCase):
    def test_win32_functions_present(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        for name in ("SetWindowsHookExW", "CallNextHookEx",
                     "UnhookWindowsHookEx", "GetAsyncKeyState",
                     "GetMessageW", "PostThreadMessageW"):
            self.assertTrue(getattr(user32, name, None), name)
        self.assertTrue(hasattr(ctypes, "WINFUNCTYPE"))

    def test_actual_low_level_hook_can_register_and_unhook(self):
        # Note: hosted CI runners may have no accessible interactive desktop.
        # A skip is surfaced to distinguish environment restrictions from PASS.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p
        )
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = ctypes.c_int
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p
        ]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t

        @callback_type
        def callback(code, wp, lp):
            return user32.CallNextHookEx(None, code, wp, lp)

        ctypes.set_last_error(0)
        hook = user32.SetWindowsHookExW(13, callback, None, 0)
        if not hook:
            err = ctypes.get_last_error()
            self.skipTest(f"runner does not permit an interactive WH_KEYBOARD_LL hook (Win32 {err})")
        try:
            self.assertTrue(hook)
        finally:
            self.assertEqual(user32.UnhookWindowsHookEx(hook), 1)


if __name__ == "__main__":
    unittest.main()
