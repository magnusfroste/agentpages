"""Counts per day: page views per site and publishes per agent. Numbers only.

stats/<YYYY-MM-DD>.json   {"visits": {site: n}, "publishes": {agent or "anonymous": n}}

No address, cookie or browser detail is kept, so a count cannot be traced back to a visitor. Counts are kept in
memory and written every STATS_FLUSH_SECONDS, and once more when the server stops. A crash loses those last seconds.
"""
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Stats:
    def __init__(self, data, keep_days=90, flush_seconds=30):
        self.dir = os.path.join(data, "stats")
        self.keep_days = max(1, keep_days)
        self.lock = threading.Lock()
        os.makedirs(self.dir, exist_ok=True)
        self.days = {}  # day -> {"visits": {}, "publishes": {}}, every day still kept
        for f in sorted(os.listdir(self.dir)):
            m = DAY.match(f)
            if m:
                try:
                    with open(os.path.join(self.dir, f), encoding="utf-8") as fh:
                        d = json.load(fh)
                    self.days[m.group(1)] = {"visits": dict(d.get("visits") or {}), "publishes": dict(d.get("publishes") or {})}
                except (OSError, ValueError):
                    pass
        self.dirty = set()
        self._prune()
        if flush_seconds > 0:
            threading.Thread(target=self._loop, args=(flush_seconds,), daemon=True).start()

    def _day(self, day=None):
        return self.days.setdefault(day or today(), {"visits": {}, "publishes": {}})

    def _add(self, kind, key):
        with self.lock:
            day = today()
            d = self._day(day)[kind]
            d[key] = d.get(key, 0) + 1
            self.dirty.add(day)

    def visit(self, site):
        self._add("visits", site)

    def publish(self, owner):
        self._add("publishes", "anonymous" if owner.startswith("anonymous:") else owner)

    def last(self, n):
        """The last n days, oldest first, each (day, {"visits": {...}, "publishes": {...}}); days with nothing are empty."""
        end = datetime.now(timezone.utc).date()
        with self.lock:
            out = []
            for i in range(n - 1, -1, -1):
                day = (end - timedelta(days=i)).isoformat()
                d = self.days.get(day) or {"visits": {}, "publishes": {}}
                out.append((day, {"visits": dict(d["visits"]), "publishes": dict(d["publishes"])}))
            return out

    def flush(self):
        with self.lock:
            dirty, self.dirty = self.dirty, set()
            snapshot = {day: json.dumps(self.days[day], ensure_ascii=False) for day in dirty if day in self.days}
        for day, text in snapshot.items():
            tmp = os.path.join(self.dir, f".{day}.{secrets.token_hex(4)}")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(tmp, os.path.join(self.dir, f"{day}.json"))
            except OSError:
                with self.lock:
                    self.dirty.add(day)
        self._prune()

    def _prune(self):
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=self.keep_days)).isoformat()
        with self.lock:
            old = [day for day in self.days if day < cutoff]
            for day in old:
                del self.days[day]
        for day in old:
            try:
                os.remove(os.path.join(self.dir, f"{day}.json"))
            except OSError:
                pass

    def _loop(self, seconds):
        while True:
            time.sleep(seconds)
            self.flush()
