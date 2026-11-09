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
KEY, KEY2 = "k" * 40, "q" * 40


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


opener = urllib.request.build_opener(NoRedirect)


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = {**os.environ, "DATA_DIR": cls.tmp.name, "PORT": str(cls.port), "MCP_KEYS": f"ada:{KEY},bob:{KEY2},short:abc",
               "PUBLIC_URL": "https://pages.example.com", "MAX_SITE_MB": "1", "MAX_FILES": "5"}
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
        req = urllib.request.Request(self.base + "/mcp", body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
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

    def test_mcp_handshake(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        req = urllib.request.Request(self.base + "/mcp", body, {"Authorization": f"Bearer {KEY}"})
        names = {t["name"] for t in json.load(urllib.request.urlopen(req))["result"]["tools"]}
        self.assertEqual(names, {"publish_site", "publish_folder", "list_sites", "read_site", "delete_site"})

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
