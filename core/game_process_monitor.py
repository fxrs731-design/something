import ctypes
import ntpath
import os
from ctypes import wintypes
from typing import Callable, Iterable


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class GameProcessMonitor:
    def __init__(self, process_names_provider: Callable[[], Iterable[str]] | None = None):
        self._process_names_provider = process_names_provider or self._windows_process_names

    def is_f1_25_running(self) -> bool:
        return any(ntpath.basename(name).casefold() == "f1_25.exe" for name in self._process_names_provider())

    @staticmethod
    def _windows_process_names() -> list[str]:
        if os.name != "nt":
            raise OSError("F1 25 process detection is supported on Windows only.")

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            raise OSError(error, "Could not enumerate Windows processes.")

        names = []
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            ctypes.set_last_error(0)
            has_entry = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            if not has_entry:
                error = ctypes.get_last_error()
                if error != 18:
                    raise OSError(error, "Could not read the Windows process snapshot.")
            while has_entry:
                names.append(entry.szExeFile)
                ctypes.set_last_error(0)
                has_entry = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
                if not has_entry:
                    error = ctypes.get_last_error()
                    if error not in (0, 18):
                        raise OSError(error, "Could not read the Windows process snapshot.")
        finally:
            kernel32.CloseHandle(snapshot)
        return names
