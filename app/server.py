#!/usr/bin/env python3
"""AgentPages: an agent publishes HTML and JavaScript, and gets a link anyone can open.

Publishing takes an agent key. Opening a site takes nothing but the address. There are no people's accounts and no
cookies on this origin, so there is nothing here for a published script to steal.

Environment:
  MCP_KEYS       name:key pairs, comma separated, one per agent (keys 32+ characters)
  PUBLIC_URL     e.g. https://pages.example.com, for the links agents get back
  MAX_SITE_MB    size limit per site (default 25)
  MAX_FILES      files per site (default 500)
  MAX_SITES      sites on this server (default 500)
  DATA_DIR/PORT  default /data, 8000

Routes: POST /mcp | POST /upload/<token> | /health | /<site>/...
"""
import base64
import binascii
import hmac
import io
import json
import os
import platform
import re
import secrets
import shlex
import tarfile
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from store import Store

VERSION = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")).read().strip()
DATA = os.environ.get("DATA_DIR", "/data")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
MAX_BYTES = int(os.environ.get("MAX_SITE_MB", "25")) * 1048576
READ_LIMIT = 2 * 1048576
UPLOAD_SECONDS = 900
SKIP_FOLDERS = {"node_modules", "__pycache__"}
BODY_LIMIT = int(MAX_BYTES * 1.4) + 1048576  # base64 and JSON around the files

TYPES = {".html": "text/html", ".htm": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
         ".json": "application/json", ".map": "application/json", ".webmanifest": "application/manifest+json",
         ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".xml": "application/xml", ".svg": "image/svg+xml",
         ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
         ".avif": "image/avif", ".ico": "image/x-icon", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav",
         ".mp4": "video/mp4", ".webm": "video/webm", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
         ".otf": "font/otf", ".wasm": "application/wasm", ".pdf": "application/pdf"}
TEXT = ("text/", "application/json", "application/manifest+json", "application/xml", "image/svg+xml")
ROOT_REF = re.compile(r"""(?:\b(?:src|href|action)\s*=\s*["']|url\(\s*["']?)/(?!/)""", re.I)
HOME = b"""<!doctype html><meta charset="utf-8"><title>AgentPages</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font:17px/1.5 system-ui,sans-serif;max-width:32em;margin:18vh auto;padding:0 1.2em}@media(prefers-color-scheme:dark){html{background:#14161a;color:#e6e6e6}}</style>
<h1>AgentPages</h1><p>Agents publish small websites here. If someone sent you a link, it goes straight to the site.</p>
"""

store = Store(DATA, MAX_BYTES, int(os.environ.get("MAX_FILES", "500")), int(os.environ.get("MAX_SITES", "500")))


def log(msg):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z {msg}", flush=True)


def load_keys():
    keys = {}
    for pair in os.environ.get("MCP_KEYS", "").split(","):
        name, _, key = pair.strip().partition(":")
        if name and len(key) >= 32:
            keys[key] = name
        elif name:
            log(f"key for {name!r} ignored: shorter than 32 characters")
    return keys


KEYS = load_keys()


def agent(header):
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    found = None
    for key, name in KEYS.items():
        if token and hmac.compare_digest(token, key):
            found = name
    return found


def url_of(name):
    return f"{PUBLIC_URL}/{name}/"


def summary(meta):
    return {"site": meta["name"], "url": url_of(meta["name"]), "title": meta["title"], "files": len(meta["files"]),
            "bytes": sum(e["size"] for e in meta["files"].values()),
            "updated": datetime.fromtimestamp(meta["updated"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}


def warnings_for(files):
    out = []
    for path, data in files.items():
        if os.path.splitext(path)[1].lower() in (".html", ".htm", ".css") and ROOT_REF.search(data.decode("utf-8", "replace")):
            out.append(f"{path} refers to something at the root of the domain (it starts with /). This site lives under its own "
                       "folder, so that will not load. Use relative paths such as assets/app.js. In Vite, set base: './'.")
    return out


def as_files(args):
    files = {}
    if args.get("html") is not None:
        if not isinstance(args["html"], str):
            raise ValueError("html must be a string.")
        files["index.html"] = args["html"].encode("utf-8")
    for path, text in (args.get("files") or {}).items():
        if not isinstance(text, str):
            raise ValueError(f"The content of {path} must be a string. Binary files go in binary_files, as base64.")
        files[path] = text.encode("utf-8")
    for path, encoded in (args.get("binary_files") or {}).items():
        try:
            files[path] = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError):
            raise ValueError(f"{path} in binary_files is not valid base64.")
    return files


def t_publish(args, who):
    files = as_files(args)
    if not files:
        raise ValueError("Nothing to publish: give html, or files with an index.html.")
    meta = store.publish(files, who, name=args.get("site") or None, title=args.get("title"), keep_existing=bool(args.get("keep_existing")))
    log(f"{who}: published {meta['name']} ({len(meta['files'])} files)")
    return {**summary(meta), "warnings": warnings_for(files)}


# A model should not have to repeat a whole game back through a tool call. publish_folder gives it one command for
# its terminal instead, so the key stays in the MCP connection and the bytes never pass through the conversation.
uploads = {}


def t_folder(args, who):
    now = time.time()
    for token in [t for t, u in list(uploads.items()) if u["expires"] < now]:
        uploads.pop(token, None)
    token = secrets.token_urlsafe(32)
    uploads[token] = {"who": who, "site": args.get("site") or None, "title": args.get("title"), "expires": now + UPLOAD_SECONDS}
    folder = shlex.quote(str(args.get("folder") or "."))
    return {"command": f"tar -czf - -C {folder} . | curl -sS --data-binary @- {PUBLIC_URL}/upload/{token}",
            "note": f"Run this once in your terminal, within {UPLOAD_SECONDS // 60} minutes. It prints the link. The folder needs an index.html "
                    "at the top. Hidden files and node_modules are left out."}


def from_upload(body):
    """A tar archive, gzipped or not, or a single HTML page. Read in memory: nothing in an archive touches the disk by its own name."""
    try:
        archive = tarfile.open(fileobj=io.BytesIO(body), mode="r:*")
    except tarfile.TarError:
        if body.lstrip()[:1] == b"<":
            return {"index.html": body}
        raise ValueError("That is neither a tar archive nor an HTML page. Did the tar command find the folder?")
    files, total = {}, 0
    with archive:
        for member in archive:
            name = member.name
            while name.startswith("./"):
                name = name[2:]
            parts = name.split("/")
            if not member.isfile() or SKIP_FOLDERS & set(parts) or any(p.startswith(".") and p != ".." for p in parts):
                continue
            total += member.size
            if total > MAX_BYTES:
                raise ValueError(f"Too large: the limit is {MAX_BYTES // 1048576} MB.")
            if len(files) >= store.max_files:
                raise ValueError(f"Too many files: the limit is {store.max_files}.")
            files[name] = archive.extractfile(member).read()
    return files


def t_list(args, who):
    return {"sites": [summary(m) for m in store.all() if m["owner"] == who]}


def t_read(args, who):
    name = args["site"]
    meta = store.get(name)
    if not meta:
        raise ValueError(f"There is no site called {name!r}.")
    path = args.get("path")
    if not path:
        return {**summary(meta), "paths": {p: e["size"] for p, e in sorted(meta["files"].items())}}
    found = store.read(name, path)
    if not found:
        raise ValueError(f"The site has no file {path!r}.")
    data = found[0]
    if len(data) > READ_LIMIT:
        raise ValueError(f"{path} is {len(data)} bytes, more than can be read here.")
    try:
        return {"path": path, "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {"path": path, "base64": base64.b64encode(data).decode()}


def t_delete(args, who):
    store.delete(args["site"], who)
    log(f"{who}: deleted {args['site']}")
    return {"deleted": args["site"]}


S, B = {"type": "string"}, {"type": "boolean"}
FILES = {"type": "object", "additionalProperties": S}
TOOLS = [
    ("publish_site", t_publish, "Publish a website and get a link anyone can open, with no sign-in. For one page give html. For several "
     "files give files, a map of relative paths to text, with index.html at the top; images and other binary files go in binary_files as "
     "base64. Leave site out the first time. To update, pass the same site: the link stays the same and visitors get the new version at "
     "once. Use relative paths between files. Anyone with the link can see everything you publish, so never include keys, passwords or "
     "private data.",
     {"html": {**S, "description": "The whole page, for a one-file site"}, "files": FILES,
      "binary_files": {**FILES, "description": "Relative paths to base64"},
      "site": {**S, "description": "The site to update, or a name you want for a new one"}, "title": S,
      "keep_existing": {**B, "description": "Keep files you do not send. Otherwise the whole site is replaced"}}, []),
    ("publish_folder", t_folder, "Publish a folder that is already on your disk, such as a game or an app you built. This is the way to "
     "publish anything larger than a page: you get one command to run in your terminal, it prints the link, and you never have to repeat "
     "the files here. Pass site to update one you published before. The link stays the same.",
     {"folder": {**S, "description": "Path to the folder, with index.html at the top"},
      "site": {**S, "description": "The site to update, or a name you want for a new one"}, "title": S}, []),
    ("list_sites", t_list, "The sites you have published, with their links.", {}, []),
    ("read_site", t_read, "A site's list of files, or one file with path. Read before you change something you published earlier.",
     {"site": S, "path": S}, ["site"]),
    ("delete_site", t_delete, "Take down a site you published. Its link stops working and it cannot be restored.", {"site": S}, ["site"]),
]
BY_NAME = {n: fn for n, fn, *_ in TOOLS}


def tool_list():
    return [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r},
             "annotations": {"readOnlyHint": n in ("list_sites", "read_site"), "destructiveHint": n == "delete_site"}}
            for n, _, d, p, r in TOOLS]


def rpc(msg, who):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "initialize":
        return ok({"protocolVersion": params.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "agentpages", "version": VERSION},
                   "instructions": "Publish a folder from your disk with publish_folder, or one page with publish_site, and give people the link. Everything published is public "
                                   "to anyone with the link. Sites run in the visitor's browser only: there is no server code or database."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_list()})
    if method == "tools/call":
        fn = BY_NAME.get(params.get("name"))
        if not fn:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {params.get('name')}"}}
        try:
            result = fn(params.get("arguments") or {}, who)
            return ok({"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}],
                       "structuredContent": result, "isError": False})
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def content_type(path):
    kind = TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    return f"{kind}; charset=utf-8" if kind.startswith(TEXT) else kind


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "agentpages"
    sys_version = ""
    timeout = 60

    def log_message(self, *args):
        pass

    def send(self, code, body=b"", kind="text/plain; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, code, value, headers=None):
        self.send(code, json.dumps(value, ensure_ascii=False).encode(), "application/json", headers)

    def redirect(self, location):
        self.send(301, b"", headers={"Location": location, "Cache-Control": "no-cache"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        raw, _, query = self.path.partition("?")
        if raw == "/health":
            return self.json(200, {"ok": True, "version": VERSION})
        if raw == "/":
            return self.send(200, HOME, "text/html; charset=utf-8", {"X-Robots-Tag": "noindex"})
        if raw == "/mcp":
            return self.send(405, b"", headers={"Allow": "POST"})
        return self.site(raw, query)

    def site(self, raw, query):
        name, slash, rest = raw[1:].partition("/")
        meta = store.get(name)
        if not meta:
            return self.send(404, b"There is no site at this address.\n")
        suffix = f"?{query}" if query else ""
        if not slash:
            return self.redirect(f"/{name}/{suffix}")
        path = urllib.parse.unquote(rest)
        files = meta["files"]
        # Only what the manifest names is ever served, and a redirect is built from that same key.
        if not path or path.endswith("/"):
            path += "index.html"
        elif path not in files and f"{path}/index.html" in files:
            return self.redirect(f"/{name}/{urllib.parse.quote(path)}/{suffix}")
        code = 200
        found = store.read(name, path)
        if not found:
            code, path = 404, "404.html"
            found = store.read(name, path)
            if not found:
                return self.send(404, b"This site has no such page.\n")
        data, entry = found
        etag = f'"{entry["sha"][:32]}"'
        headers = {"ETag": etag, "Cache-Control": "no-cache", "X-Robots-Tag": "noindex", "Referrer-Policy": "strict-origin-when-cross-origin"}
        if code == 200 and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            return self.end_headers()
        return self.send(code, data, content_type(path), headers)

    def upload(self, token, body):
        job = uploads.get(token)
        if not job or job["expires"] < time.time():
            return self.send(404, b"This upload link is used or too old. Ask publish_folder for a new one.\n")
        try:
            files = from_upload(body)
            meta = store.publish(files, job["who"], name=job["site"], title=job["title"])
        except (ValueError, tarfile.TarError, EOFError, OSError) as e:
            return self.send(400, f"Not published: {e}\n".encode())
        uploads.pop(token, None)
        log(f"{job['who']}: uploaded {meta['name']} ({len(meta['files'])} files)")
        lines = [url_of(meta["name"])] + [f"Warning: {w}" for w in warnings_for(files)]
        return self.send(200, ("\n".join(lines) + "\n").encode())

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > BODY_LIMIT:
            self.close_connection = True
            return self.json(413, {"error": f"Too large. A site can be at most {MAX_BYTES // 1048576} MB."})
        body = self.rfile.read(n)
        route = self.path.partition("?")[0]
        if route.startswith("/upload/"):
            return self.upload(route[8:], body)
        if route != "/mcp":
            return self.send(404, b"not found")
        who = agent(self.headers.get("Authorization", ""))
        if not who:
            return self.json(401, {"error": "Missing or wrong key. Send Authorization: Bearer <key>."}, {"WWW-Authenticate": "Bearer"})
        try:
            msg = json.loads(body or b"null")
        except ValueError:
            return self.json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}})
        batch = msg if isinstance(msg, list) else [msg]
        answers = [a for a in (rpc(m, who) for m in batch if isinstance(m, dict)) if a is not None]
        if not answers:
            return self.send(202, b"", "application/json")
        return self.json(200, answers if isinstance(msg, list) else answers[0])


def main():
    if not KEYS:
        log("MCP_KEYS has no name:key pair with a key of 32+ characters: agents cannot publish.")
    if not PUBLIC_URL:
        log("PUBLIC_URL is not set: agents get links without the domain.")
    port = int(os.environ.get("PORT", "8000"))
    log(f"AgentPages {VERSION} on :{port}, {PUBLIC_URL or '(no PUBLIC_URL)'}, {len(store.all())} site(s), "
        f"agents: {', '.join(sorted(KEYS.values())) or '-'}, python {platform.python_version()}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
