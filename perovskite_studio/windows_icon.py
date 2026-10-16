"""Give Windows a full-resolution icon instead of Tk's 32 px class icon."""
import ctypes
from ctypes import wintypes


def install_taskbar_icon(root, icon_path):
    """Set ICON_BIG after Tk creates its native wrapper; retain owned handles."""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                 wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL
    handles = []

    def apply(event):
        if event.widget is not root or handles:
            return
        hwnd = user32.GetParent(root.winfo_id())
        if not hwnd:
            return
        # Load the actual 256 px ICO entry, never upscale Tk's 32 px icon.
        # The shell can downsample this for taskbar/Alt+Tab at any usual DPI.
        small_size = user32.GetSystemMetrics(49)  # SM_CXSMICON
        for kind, size in ((0, small_size), (1, 256)):
            handle = user32.LoadImageW(None, str(icon_path), 1, size, size, 0x10)
            if handle:
                handles.append(handle)
                user32.SendMessageW(hwnd, 0x80, kind, handle)  # WM_SETICON

    def cleanup(event):
        if event.widget is root:
            for handle in handles:
                user32.DestroyIcon(handle)
            handles.clear()

    root.bind('<Map>', apply, add='+')
    root.bind('<Destroy>', cleanup, add='+')
