"""Monitor script for a Hermes cron job: prints the AgentPages moderation feed.

The admin key is read from this profile's MCP entry for pages, so there is no second copy of it. The output has no
clock in it, so Hermes only wakes the model when it changes.

Put it in $HERMES_HOME/scripts/ and see README.md next to it."""
import os
import re
import urllib.error
import urllib.request

SERVER = os.environ.get("PAGES_MCP_NAME", "pages")
CONFIG = os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "config.yaml")

text = open(CONFIG, encoding="utf-8").read()
block = re.search(rf"^  {re.escape(SERVER)}:\n((?:    .*\n)+)", text, re.M)
url = re.search(r"url:\s*(\S+)", block.group(1)) if block else None
key = re.search(r"Authorization:\s*Bearer\s+(\S+)", block.group(1)) if block else None
if not url or not key:
    print(f"pages-watch: no MCP server {SERVER!r} with a Bearer key in {CONFIG}")
else:
    base = url.group(1).strip("\"'").rsplit("/mcp", 1)[0]
    req = urllib.request.Request(base + "/moderation/feed",
                                 headers={"Authorization": "Bearer " + key.group(1), "User-Agent": "hermes-pages-watch/1"})
    try:
        print(urllib.request.urlopen(req, timeout=30).read().decode())
    except urllib.error.HTTPError as e:
        print(f"pages-watch: the feed answered {e.code}")
    except OSError as e:
        print(f"pages-watch: the feed could not be reached ({type(e).__name__})")
