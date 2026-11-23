"""Sites on disk. No database.

sites/<name>/site.json          who owns it, its title, its version number, and the manifest: path -> {sha, size}
sites/<name>/blobs/<sha>        the bytes
sites/<name>/versions/<n>.json  an earlier site.json, kept so a publish can be undone
bin/<id>/                       a deleted site, its folder moved here whole, with deleted.json: who, when, and the
                                time it is purged. Gone from sites/, so it stops being served at once
moved/<name>.json               where a deleted site's old links now go, and who set it

Blobs are shared by every version that names them, so a version that changed one file costs that file.

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
RESERVED = {"mcp", "upload", "moderation", "health", "api", "admin", "static", "assets", "www"}
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


BIN_ID = re.compile(r"^([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.\d+\.[0-9a-f]{8}$")
URL = re.compile(r"^https?://[^\s<>\"'\\]+$")


def check_url(url):
    if not isinstance(url, str) or len(url) > 500 or not URL.match(url):
        raise ValueError("moved_to must be a full http:// or https:// address.")
    return url


class Store:
    def __init__(self, data, max_bytes, max_files, max_sites, keep_versions=10, bin_days=30, clock=time.time):
        self.root = os.path.join(data, "sites")
        self.bin_root = os.path.join(data, "bin")
        self.moved_root = os.path.join(data, "moved")
        self.max_bytes, self.max_files, self.max_sites = max_bytes, max_files, max_sites
        self.keep_versions = max(0, keep_versions)
        self.bin_days, self.clock = max(0, bin_days), clock
        self.lock = threading.Lock()
        for d in (self.root, self.bin_root, self.moved_root):
            os.makedirs(d, exist_ok=True)

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

    def versions(self, name):
        """Earlier versions of a site, newest first. Each is a site.json as it was."""
        d = os.path.join(self._dir(name), "versions")
        out = []
        for f in os.listdir(d) if os.path.isdir(d) else []:
            if re.fullmatch(r"\d+\.json", f):
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        out.append(json.load(fh))
                except (OSError, ValueError):
                    pass
        return sorted(out, key=lambda m: m.get("version", 0), reverse=True)

    def _write_live(self, d, existing, manifest, owner, name, title):
        """Keep the live site.json as a version, write the new one, and drop what no kept version needs.
        Called with the lock held, after the new blobs are on disk."""
        now = int(time.time())
        number = (existing or {}).get("version", 1 if existing else 0) + 1
        if existing and self.keep_versions:
            vdir = os.path.join(d, "versions")
            os.makedirs(vdir, exist_ok=True)
            old = {**existing, "version": existing.get("version", 1)}
            tmp = os.path.join(vdir, f".{old['version']}.{secrets.token_hex(4)}")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(old, f, ensure_ascii=False)
            os.replace(tmp, os.path.join(vdir, f"{old['version']}.json"))
        meta = {"name": name, "owner": owner, "title": title or "", "version": number,
                "created": (existing or {}).get("created", now), "updated": now, "files": manifest}
        tmp = os.path.join(d, f".site.{secrets.token_hex(4)}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(d, "site.json"))

        kept = self.versions(name)
        vdir = os.path.join(d, "versions")
        for old in kept[self.keep_versions:]:
            try:
                os.remove(os.path.join(vdir, f"{old['version']}.json"))
            except OSError:
                pass
        live = {e["sha"] for m in [meta] + kept[:self.keep_versions] for e in m["files"].values()}
        blobs = os.path.join(d, "blobs")
        for blob in os.listdir(blobs):
            if blob not in live and not blob.startswith("."):
                os.remove(os.path.join(blobs, blob))
        return meta

    def restore(self, name, owner, version):
        """Make an earlier version live again. The version that was live is kept, so a restore can be undone too."""
        with self.lock:
            existing = self.get(name)
            if not existing:
                raise ValueError(f"There is no site called {name!r}.")
            if existing["owner"] != owner:
                raise ValueError(f"The site {name!r} belongs to another agent.")
            old = next((m for m in self.versions(name) if m.get("version") == version), None)
            if not old:
                have = [m["version"] for m in self.versions(name)]
                raise ValueError(f"{name!r} has no version {version}. Earlier versions: {have or 'none'} (list_versions shows them).")
            blobs = os.path.join(self._dir(name), "blobs")
            if any(not os.path.exists(os.path.join(blobs, e["sha"])) for e in old["files"].values()):
                raise ValueError(f"Version {version} cannot be restored: some of its files are gone.")
            return self._write_live(self._dir(name), existing, dict(old["files"]), owner, name, old.get("title"))

    def _new_name(self):
        for _ in range(50):
            name = f"{secrets.choice(ADJECTIVES)}-{secrets.choice(NOUNS)}-{secrets.token_hex(3)}"
            if not os.path.exists(self._dir(name)) and not self._holder(name):
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
            if name and not existing:
                self._check_free(name, owner)
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

            title = (title if title is not None else (existing or {}).get("title")) or ""
            meta = self._write_live(d, existing, manifest, owner, name, title)
            self._drop_moved(name)
            return meta

    # --- the bin -------------------------------------------------------------------------------------------
    # A delete moves the site's folder, versions and all, into bin/ in one rename, so the link answers 404 at once
    # and nothing is copied. It stays there for bin_days, then purge() removes it for good. The date it is purged is
    # written when it is deleted, so changing BIN_DAYS later does not break the date an agent was given.

    def binned(self, owner=None, name=None):
        """What is in the bin and not yet past its date, newest first. owner None: everyone's."""
        now, out = self.clock(), []
        for d in os.listdir(self.bin_root):
            m = BIN_ID.match(d)
            if not m or (name and m.group(1) != name):
                continue
            try:
                with open(os.path.join(self.bin_root, d, "deleted.json"), encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                continue
            if rec.get("purge_at", 0) > now and (owner is None or rec.get("owner") == owner):
                out.append(rec)
        return sorted(out, key=lambda r: (r.get("at", r["deleted"]), r["id"]), reverse=True)

    def moved(self, name):
        """{"url", "owner"} when a deleted site's old links go somewhere else, or None."""
        if not NAME.match(name or ""):
            return None
        try:
            with open(os.path.join(self.moved_root, f"{name}.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _drop_moved(self, name):
        try:
            os.remove(os.path.join(self.moved_root, f"{name}.json"))
        except OSError:
            pass

    def _set_moved(self, name, owner, url):
        tmp = os.path.join(self.moved_root, f".{name}.{secrets.token_hex(4)}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"name": name, "owner": owner, "url": url, "set": int(self.clock())}, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.moved_root, f"{name}.json"))

    def _holder(self, name):
        """The owner a free name is held for: its newest copy in the bin, else its moved_to. None if nobody's."""
        held = self.binned(name=name)
        if held:
            return held[0]["owner"], f"is in the bin until {when(held[0]['purge_at'])}"
        moved = self.moved(name)
        return (moved["owner"], "points to the site's new address") if moved else None

    def _check_free(self, name, owner):
        """A name with no live site is held for the agent who deleted it while its copy is in the bin or its old links
        point somewhere else, so those links never land on someone else's site."""
        held = self._holder(name)
        if held and held[0] != owner:
            raise ValueError(f"The name {name!r} belongs to another agent: it {held[1]}. Leave site out to get a free one.")

    def delete(self, name, owner, by="", taken_down=False, moved_to=None):
        """Move a site into the bin. Returns its record, or None when bin_days is 0 and it is gone for good.
        With moved_to, its old links redirect there from now on. A name with no site, but free or already this
        owner's, can be given a moved_to alone (meta None and no record)."""
        if moved_to is not None:
            check_url(moved_to)
        with self.lock:
            meta = self.get(name)
            if not meta:
                if moved_to is not None and NAME.match(name or ""):
                    self._check_free(name, owner)
                    self._set_moved(name, owner, moved_to)
                    return None
                raise ValueError(f"There is no site called {name!r}.")
            if meta["owner"] != owner:
                raise ValueError(f"The site {name!r} belongs to another agent.")
            if moved_to is not None:
                self._set_moved(name, owner, moved_to)
            if not self.bin_days:
                shutil.rmtree(self._dir(name))
                return None
            at = self.clock()
            now = int(at)
            rec = {"id": f"{name}.{now}.{secrets.token_hex(4)}", "name": name, "owner": owner, "title": meta.get("title", ""),
                   "version": meta.get("version", 1), "files": len(meta["files"]),
                   "bytes": sum(e["size"] for e in meta["files"].values()), "by": by, "taken_down": bool(taken_down),
                   "deleted": now, "at": at, "purge_at": now + self.bin_days * 86400}
            # The record goes in first and the folder moves last, in one rename: a crash leaves a live site or a binned one.
            with open(os.path.join(self._dir(name), "deleted.json"), "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
            os.rename(self._dir(name), os.path.join(self.bin_root, rec["id"]))
            return rec

    def undelete(self, name, owner, deleted_id=None, as_site=None, admin=False):
        """Bring a site back from the bin, with its earlier versions, at its own name or at as_site.
        Returns (site.json, the bin record)."""
        with self.lock:
            items = [r for r in self.binned(name=name) if admin or r["owner"] == owner]
            if deleted_id:
                items = [r for r in items if r["id"] == deleted_id]
            if not items:
                raise ValueError(f"There is nothing called {name!r} in your bin{' with that deleted_id' if deleted_id else ''}. "
                                 "list_deleted_sites shows what is there. After its date a deleted site is gone for good.")
            rec = items[0]
            if rec.get("taken_down") and not admin:
                raise ValueError(f"An admin took {name!r} down. Only an admin can bring it back.")
            target = check_name(as_site or name)
            if self.get(target):
                raise ValueError(f"The name {target!r} has a live site now. Delete that one first, or bring this one back "
                                 "under another name with as_site.")
            if target != name:
                self._check_free(target, rec["owner"])
            if len(self.all()) >= self.max_sites:
                raise ValueError(f"This server is full ({self.max_sites} sites). Delete one you no longer need.")
            os.rename(os.path.join(self.bin_root, rec["id"]), self._dir(target))
            try:
                os.remove(os.path.join(self._dir(target), "deleted.json"))
            except OSError:
                pass
            meta = self.get(target)
            if target != name:
                meta["name"] = target
                tmp = os.path.join(self._dir(target), f".site.{secrets.token_hex(4)}")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False)
                os.replace(tmp, os.path.join(self._dir(target), "site.json"))
            moved = self.moved(target)
            if moved and moved["owner"] == rec["owner"]:
                self._drop_moved(target)
            return meta, rec

    def purge(self):
        """Remove for good what has been in the bin past its date. Returns the records removed."""
        now, gone = self.clock(), []
        with self.lock:
            for d in os.listdir(self.bin_root):
                if not BIN_ID.match(d):
                    continue
                path = os.path.join(self.bin_root, d)
                try:
                    with open(os.path.join(path, "deleted.json"), encoding="utf-8") as f:
                        rec = json.load(f)
                except (OSError, ValueError):
                    continue
                if rec.get("purge_at", 0) <= now:
                    shutil.rmtree(path, ignore_errors=True)
                    gone.append(rec)
        return gone


def when(ts):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))
