import ctypes
import ctypes.wintypes as wintypes
import threading
import time

VK_B = 0x42
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1

class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('vkCode', ctypes.c_uint32), ('scanCode', ctypes.c_uint32),
                ('flags', ctypes.c_uint32), ('time', ctypes.c_uint32),
                ('dwExtraInfo', ctypes.c_void_p)]

proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p)

ctrl = False
shift = False

def cb(nCode, wParam, lParam):
    global ctrl, shift
    try:
        if nCode >= 0:
            s = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT))
            vk = s.contents.vkCode
            down = wParam in (0x0100, 0x0104)
            up = wParam in (0x0101, 0x0105)
            
            if vk in (VK_LCONTROL, VK_RCONTROL):
                ctrl = down
                print(f"Ctrl: {down}", flush=True)
            elif vk in (VK_LSHIFT, VK_RSHIFT):
                shift = down
                print(f"Shift: {down}", flush=True)
            elif vk == VK_B and down and ctrl and shift:
                print(">>> TRIGGER! Ctrl+Shift+B detected <<<", flush=True)
            elif vk == VK_B and down:
                print(f"B pressed but ctrl={ctrl} shift={shift}", flush=True)
    except Exception as e:
        print('ERR:', e, flush=True)
    return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

ref = proc_type(cb)
user32 = ctypes.windll.user32
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
hook = user32.SetWindowsHookExW(13, ref, None, 0)
print("Hook installed. Press Ctrl+Shift+B to test. Ctrl+C to exit.", flush=True)
if hook:
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(msg)
        user32.DispatchMessageW(msg)
print("Done", flush=True)