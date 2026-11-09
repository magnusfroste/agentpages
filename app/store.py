"""Sites on disk. No database.

sites/<name>/site.json   who owns it, its title, and the manifest: path -> {sha, size}
sites/<name>/blobs/<sha> the bytes

A request path is looked up in the manifest and never reaches the disk, so there is no path to traverse. A
publish writes the blobs first and swaps site.json last, so a visitor sees the old site or the new one.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time

NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RESERVED = {"mcp", "upload", "moderation", "health", "api", "admin", "static", "assets"}
BAD_SEGMENT = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
ADJECTIVES = """amber bold brave bright calm clever cosmic crisp dapper eager fancy gentle golden happy jolly keen
lively lucky mellow merry nimble plucky quiet rapid rosy shiny snowy sunny swift tidy vivid witty""".split()
NOUNS = """badger biscuit brook cedar cloud comet cottage ember falcon fern fjord harbor heron island juniper kite
lantern lokum maple meadow otter pebble pine puffin raven river sparrow thistle tulip valley walrus willow""".split()


def check_path(path):
    if not isinstance(path, str) or not path or len(path) > 400 or path.count("/") > 20:
        raise ValueError(f"Bad file path: {path!r}")
    for part in path.split("/"):
        if not part or part in (".", "..") or part.startswith(".") or BAD_SEGMENT.search(part):
            raise ValueError(f"Bad file path: {path!r}. Use relative paths such as index.html or img/logo.png, with no hidden files.")
    return path


def check_name(name):
    if not isinstance(name, str) or not NAME.match(name):
        raise ValueError("A site name is 1-63 lower-case letters, digits and hyphens, starting and ending with a letter or digit.")
    if name in RESERVED:
        raise ValueError(f"The name {name!r} is reserved.")
    return name


class Store:
    def __init__(self, data, max_bytes, max_files, max_sites):
        self.root = os.path.join(data, "sites")
        self.max_bytes, self.max_files, self.max_sites = max_bytes, max_files, max_sites
        self.lock = threading.Lock()
        os.makedirs(self.root, exist_ok=True)

    def _dir(self, name):
        return os.path.join(self.root, name)

    def get(self, name):
        if not NAME.match(name or ""):
            return None
        try:
            with open(os.path.join(self._dir(name), "site.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def all(self):
        return [m for m in (self.get(n) for n in sorted(os.listdir(self.root))) if m]

    def read(self, name, path):
        """(bytes, entry) or None. A publish may remove a blob between the manifest and the read: look once more."""
        for _ in range(2):
            meta = self.get(name)
            entry = meta and meta["files"].get(path)
            if not entry:
                return None
            try:
                with open(os.path.join(self._dir(name), "blobs", entry["sha"]), "rb") as f:
                    return f.read(), entry
            except FileNotFoundError:
                continue
        return None

    def _new_name(self):
        for _ in range(50):
            name = f"{secrets.choice(ADJECTIVES)}-{secrets.choice(NOUNS)}-{secrets.token_hex(3)}"
            if not os.path.exists(self._dir(name)):
                return name
        raise ValueError("Could not find a free name.")

    def publish(self, files, owner, name=None, title=None, keep_existing=False, max_bytes=None):
        """files: path -> bytes. The whole site is replaced, unless keep_existing. Returns the new site.json."""
        with self.lock:
            existing = self.get(name) if name else None
            if name:
                check_name(name)
            if existing and existing["owner"] != owner:
                raise ValueError(f"The name {name!r} belongs to another agent. Leave site out to get a free one.")
            if keep_existing and not existing:
                raise ValueError("keep_existing needs a site you have already published.")
            if not existing and len(self.all()) >= self.max_sites:
                raise ValueError(f"This server is full ({self.max_sites} sites). Delete one you no longer need.")
            for path in files:
                check_path(path)

            d = self._dir(name) if name else None
            kept = dict(existing["files"]) if keep_existing else {}
            manifest, new = dict(kept), {}
            for path, data in files.items():
                sha = hashlib.sha256(data).hexdigest()
                manifest[path] = {"sha": sha, "size": len(data)}
                new[sha] = data
            if "index.html" not in manifest:
                raise ValueError("A site needs an index.html at the top.")
            if len(manifest) > self.max_files:
                raise ValueError(f"Too many files: {len(manifest)}, the limit is {self.max_files}.")
            total = sum(e["size"] for e in manifest.values())
            limit = max_bytes or self.max_bytes
            if total > limit:
                raise ValueError(f"Too large: {total // 1048576} MB, the limit is {limit // 1048576} MB.")

            name = name or self._new_name()
            d = self._dir(name)
            blobs = os.path.join(d, "blobs")
            os.makedirs(blobs, exist_ok=True)
            for sha, data in new.items():
                tmp = os.path.join(blobs, f".{sha}.{secrets.token_hex(4)}")
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, os.path.join(blobs, sha))

            now = int(time.time())
            meta = {"name": name, "owner": owner, "title": (title if title is not None else (existing or {}).get("title")) or "",
                    "created": (existing or {}).get("created", now), "updated": now, "files": manifest}
            tmp = os.path.join(d, f".site.{secrets.token_hex(4)}")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
            os.replace(tmp, os.path.join(d, "site.json"))

            live = {e["sha"] for e in manifest.values()}
            for blob in os.listdir(blobs):
                if blob not in live and not blob.startswith("."):
                    os.remove(os.path.join(blobs, blob))
            return meta

    def delete(self, name, owner):
        with self.lock:
            meta = self.get(name)
            if not meta:
                raise ValueError(f"There is no site called {name!r}.")
            if meta["owner"] != owner:
                raise ValueError(f"The site {name!r} belongs to another agent.")
            shutil.rmtree(self._dir(name))
