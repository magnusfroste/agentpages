"""Counts per day: page views, visitors and the sites they came from per site, publishes per agent. Numbers only.

stats/<YYYY-MM-DD>.json   {"visits": {site: n}, "visitors": {site: n}, "visitors_all": n,
                           "referrers": {site: {domain: n}}, "publishes": {agent or "anonymous": n}}

No address, cookie or browser detail is kept, so a count cannot be traced back to a visitor. A visitor is told apart
from another by a hash of their address and browser name with a random salt that lives in memory only and is
replaced at midnight UTC: once it is gone, not even this server can tell whether two days' visitors were the same
person. So "visitors" means people per day, and a restart starts the day's count of who was seen afresh (someone
who comes back after it counts twice). A referrer is kept as its domain only, never the page or the query.

Counts are kept in memory and written every STATS_FLUSH_SECONDS, and once more when the server stops. A crash loses
those last seconds.
"""
import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
MAX_SEEN = 200_000       # visitor hashes held for a day; past it a visitor counts as new, so memory stays bounded
MAX_REFERRERS = 50       # domains per site and day; the rest count as "other"


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def empty():
    return {"visits": {}, "visitors": {}, "visitors_all": 0, "referrers": {}, "publishes": {}}


def copy(d):
    return {"visits": dict(d["visits"]), "visitors": dict(d["visitors"]), "visitors_all": d["visitors_all"],
            "referrers": {site: dict(r) for site, r in d["referrers"].items()}, "publishes": dict(d["publishes"])}


def loaded(d):
    out = empty()
    for k in ("visits", "visitors", "publishes"):
        out[k] = {str(s): int(n) for s, n in (d.get(k) or {}).items()}
    out["visitors_all"] = int(d.get("visitors_all") or 0)
    out["referrers"] = {str(s): {str(h): int(n) for h, n in r.items()} for s, r in (d.get("referrers") or {}).items()}
    return out


class Stats:
    def __init__(self, data, keep_days=90, flush_seconds=30):
        self.dir = os.path.join(data, "stats")
        self.keep_days = max(1, keep_days)
        self.lock = threading.Lock()
        os.makedirs(self.dir, exist_ok=True)
        self.days = {}  # day -> empty() shape, every day still kept
        for f in sorted(os.listdir(self.dir)):
            m = DAY.match(f)
            if m:
                try:
                    with open(os.path.join(self.dir, f), encoding="utf-8") as fh:
                        self.days[m.group(1)] = loaded(json.load(fh))
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
        self.dirty = set()
        self.salt_day, self.salt, self.seen, self.seen_count = None, b"", {}, 0
        self._prune()
        if flush_seconds > 0:
            threading.Thread(target=self._loop, args=(flush_seconds,), daemon=True).start()

    def _day(self, day=None):
        return self.days.setdefault(day or today(), empty())

    def _first(self, scope, key):
        """True the first time this salted key is seen today within scope (a site, or "" for the whole server)."""
        seen = self.seen.setdefault(scope, set())
        if key in seen:
            return False
        if self.seen_count < MAX_SEEN:
            seen.add(key)
            self.seen_count += 1
        return True

    def visit(self, site, visitor=None, referrer=None):
        """One page view. visitor is any string that tells visitors apart (address and browser name); it is hashed
        with today's salt and never stored. referrer is a domain, or None for a visit with no other site behind it."""
        with self.lock:
            day = today()
            d = self._day(day)
            d["visits"][site] = d["visits"].get(site, 0) + 1
            if visitor is not None:
                if self.salt_day != day:
                    self.salt_day, self.salt, self.seen, self.seen_count = day, secrets.token_bytes(32), {}, 0
                key = hashlib.sha256(self.salt + visitor.encode("utf-8", "replace")).digest()[:16]
                if self._first(site, key):
                    d["visitors"][site] = d["visitors"].get(site, 0) + 1
                if self._first("", key):
                    d["visitors_all"] += 1
            if referrer:
                r = d["referrers"].setdefault(site, {})
                if referrer not in r and len(r) >= MAX_REFERRERS:
                    referrer = "other"
                r[referrer] = r.get(referrer, 0) + 1
            self.dirty.add(day)

    def publish(self, owner):
        with self.lock:
            day = today()
            p = self._day(day)["publishes"]
            key = "anonymous" if owner.startswith("anonymous:") else owner
            p[key] = p.get(key, 0) + 1
            self.dirty.add(day)

    def last(self, n):
        """The last n days, oldest first, each (day, counts in the shape of empty()); days with nothing are empty."""
        end = datetime.now(timezone.utc).date()
        with self.lock:
            out = []
            for i in range(n - 1, -1, -1):
                day = (end - timedelta(days=i)).isoformat()
                out.append((day, copy(self.days[day]) if day in self.days else empty()))
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
