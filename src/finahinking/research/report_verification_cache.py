"""Short-lived successful integrity checks, bounded and local to one repository."""
import stat
import threading
import time
from collections import OrderedDict
from pathlib import Path


class ReportVerificationCache:
    def __init__(self, *, capacity=64, ttl=2.0, clock=time.monotonic, root=None):
        if type(capacity) is not int or capacity < 1 or ttl <= 0:
            raise ValueError("invalid verification cache limits")
        self.root = Path(root).absolute() if root is not None else None
        self.capacity, self.ttl, self.clock = capacity, ttl, clock
        self._items = OrderedDict()
        self._lock = threading.Lock()
        self._hits = self._misses = self._invalidations = 0

    def _stamps(self, paths):
        nodes = set()
        for item in paths:
            path = Path(item).absolute()
            nodes.update((path, *path.parents))
        stamps = []
        for path in sorted(nodes):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) and (self.root is None or path.is_relative_to(self.root)):
                raise ValueError("symlink in report tree")
            # Parent directory contents may change for unrelated SQLite WAL files.
            # Bind their identity/type; bind full timestamps and size for files.
            details = () if stat.S_ISDIR(info.st_mode) else (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            stamps.append((str(path), info.st_dev, info.st_ino, info.st_mode, *details))
        return tuple(stamps)

    def check(self, key, paths, verify):
        try:
            before = self._stamps(paths)
        except FileNotFoundError:
            return "MISSING"
        except (OSError, ValueError):
            return "CORRUPT"
        now = self.clock()
        with self._lock:
            cached = self._items.get(key)
            if cached and now-cached[0] < self.ttl and cached[1] == before:
                self._hits += 1
                self._items.move_to_end(key)
                return "AVAILABLE"
            self._misses += 1
            if cached:
                self._invalidations += 1
                del self._items[key]
        result = verify()
        if result == "AVAILABLE":
            try:
                after = self._stamps(paths)
            except FileNotFoundError:
                return "MISSING"
            except (OSError, ValueError):
                return "CORRUPT"
            if before != after:
                return "CORRUPT"
            with self._lock:
                self._items[key] = (self.clock(), after)
                self._items.move_to_end(key)
                while len(self._items) > self.capacity:
                    self._items.popitem(last=False)
        return result

    def clear(self):
        with self._lock:
            self._items.clear()

    def statistics(self):
        with self._lock:
            return {"hits": self._hits, "misses": self._misses, "invalidations": self._invalidations, "entries": len(self._items), "capacity": self.capacity, "ttl_seconds": self.ttl}
