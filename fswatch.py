"""Block until a directory changes: kqueue (macOS/BSD), inotify (Linux),
FindFirstChangeNotification (Windows), cheap mtime check elsewhere.

Used instead of sleep-polling so idle processes cost no CPU. Atomic writes (rename into the
directory) and deletes both wake it."""
import ctypes
import os
import select
import sys
import time


class DirWatch:
    def __init__(self, path: str):
        self.path = path
        self._kq = None
        self._fd = None
        self._ino = None
        self._win = None
        self._k32 = None
        self._mt = self._mtime()
        try:
            if sys.platform == "win32":
                k32 = ctypes.windll.kernel32
                k32.FindFirstChangeNotificationW.restype = ctypes.c_void_p
                k32.FindFirstChangeNotificationW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint32]
                k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                k32.WaitForSingleObject.restype = ctypes.c_uint32
                k32.FindNextChangeNotification.argtypes = [ctypes.c_void_p]
                k32.FindCloseChangeNotification.argtypes = [ctypes.c_void_p]
                # FILE_NAME | DIR_NAME | SIZE | LAST_WRITE
                h = k32.FindFirstChangeNotificationW(path, 0, 0x1 | 0x2 | 0x8 | 0x10)
                if h and h != ctypes.c_void_p(-1).value:
                    self._win, self._k32 = h, k32
            elif hasattr(select, "kqueue"):
                flag = getattr(os, "O_EVTONLY", 0x8000) if sys.platform == "darwin" else os.O_RDONLY
                self._fd = os.open(path, flag)
                self._kq = select.kqueue()
                ev = select.kevent(self._fd, filter=select.KQ_FILTER_VNODE,
                                   flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                                   fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB
                                   | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE)
                self._kq.control([ev], 0, 0)
            elif sys.platform.startswith("linux"):
                libc = ctypes.CDLL(None, use_errno=True)
                fd = libc.inotify_init1(os.O_NONBLOCK | 0x80000)
                if fd >= 0 and libc.inotify_add_watch(fd, os.fsencode(path), 0x2 | 0x8 | 0x80 | 0x100 | 0x200) >= 0:
                    self._ino = fd
                elif fd >= 0:
                    os.close(fd)
        except Exception:
            self.close()

    def _mtime(self) -> int:
        try:
            return os.stat(self.path).st_mtime_ns
        except OSError:
            return 0

    def wait(self, timeout: float) -> bool:
        """True when the directory changed (or may have), False on timeout."""
        if self._win is not None:
            r = self._k32.WaitForSingleObject(self._win, int(timeout * 1000))
            if r == 0:
                self._k32.FindNextChangeNotification(self._win)
                return True
            if r == 0x102:  # WAIT_TIMEOUT
                return False
            self.close()
        if self._kq is not None:
            try:
                return bool(self._kq.control(None, 1, timeout))
            except OSError:
                self.close()
        if self._ino is not None:
            r, _, _ = select.select([self._ino], [], [], timeout)
            if r:
                try:
                    os.read(self._ino, 65536)
                except OSError:
                    pass
                return True
            return False
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            time.sleep(min(2.0, max(0.05, end - time.monotonic())))
            m = self._mtime()
            if m != self._mt:
                self._mt = m
                return True
        return False

    def close(self) -> None:
        if self._win is not None:
            try:
                self._k32.FindCloseChangeNotification(self._win)
            except Exception:
                pass
            self._win = None
        if self._kq is not None:
            try:
                self._kq.close()
            except Exception:
                pass
            self._kq = None
        for attr in ("_fd", "_ino"):
            fd = getattr(self, attr)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, attr, None)
