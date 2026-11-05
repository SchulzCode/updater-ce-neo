"""Return freed heap pages to the operating system.

Qt and CPython release widget/Python objects to their allocators, but the
allocator keeps the pages committed: the process then looks exactly as large
as before even though the memory is free. The tray idle-shed only helps if
those pages actually go back, so after the window is destroyed this module
asks the platform's allocator to release them.

All of it is best-effort. A host without the symbol, a hardened runtime that
refuses the call, or an unusual libc simply returns False and the shed still
happens -- it just will not lower the resident size as far as it could.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys

logger = logging.getLogger(__name__)


def release_memory_to_os() -> bool:
    """Ask the C allocator to hand free pages back. True when it was asked."""
    try:
        if sys.platform == "darwin":
            libc = ctypes.CDLL(None)
            relief = getattr(libc, "malloc_zone_pressure_relief", None)
            if relief is None:
                return False
            # (zone, goal): a NULL zone means every zone, goal 0 means "as much
            # as possible". It returns the bytes released.
            relief.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            relief.restype = ctypes.c_size_t
            released = relief(None, ctypes.c_size_t(0))
            logger.debug("malloc_zone_pressure_relief released %s bytes", released)
            return True

        if os.name == "nt":
            # Decommit what is no longer in use and trim the working set. The
            # pages fault back in on demand; without this the freed UI heap
            # stays counted in the process working set indefinitely.
            psapi = ctypes.windll.psapi
            psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
            psapi.EmptyWorkingSet.restype = ctypes.c_int
            return bool(psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess()))

        if sys.platform.startswith("linux"):
            libc = ctypes.CDLL("libc.so.6")
            trim = getattr(libc, "malloc_trim", None)
            if trim is None:
                return False
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            trim(0)
            return True
    except Exception:
        logger.debug("Could not release memory to the operating system", exc_info=True)
    return False
