"""Atomic, bounded caches for public market data. Never stores user portfolios."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import threading
import time

ROOT = Path(os.environ.get("BUYNTIQ_CACHE_DIR", Path(tempfile.gettempdir()) / "buyntiq-v4"))
_locks = [threading.RLock() for _ in range(64)]


def path_for(namespace, key):
    digest = hashlib.sha256(str(key).encode()).hexdigest()
    return ROOT / namespace / (digest + ".json")


def lock_for(key):
    return _locks[int(hashlib.sha256(str(key).encode()).hexdigest(), 16) % len(_locks)]


def read(namespace, key, ttl):
    path = path_for(namespace, key)
    try:
        if time.time() - path.stat().st_mtime > ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write(namespace, key, value):
    path = path_for(namespace, key)
    temp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
            temp = Path(f.name)
            json.dump(value, f, allow_nan=False, separators=(",", ":"))
        os.replace(temp, path)
        # Limit a cache namespace so a hosted instance cannot grow indefinitely.
        entries = list(path.parent.glob("*.json"))
        if len(entries) > 1200:
            for old in sorted(entries, key=lambda p: p.stat().st_mtime)[:100]:
                old.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass  # Read-only or temporary hosting must not break research.
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
