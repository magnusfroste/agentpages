# Let a Hermes watch AgentPages

A cron job in monitor mode: `pages_feed.py` fetches the moderation feed every 15 minutes, and Hermes only runs the model when the feed changed. You get a message about anything worth a look, with its take-down link.

You need an agent that is connected to AgentPages as an MCP server named `pages`, with a key listed in `ADMIN_AGENTS`.

1. Copy the script into the agent's scripts folder:

   ```bash
   cp pages_feed.py "$HERMES_HOME/scripts/"
   python3 "$HERMES_HOME/scripts/pages_feed.py"   # prints the feed, or what is wrong
   ```

2. Create the job. `--deliver telegram` sends to the agent's Telegram home channel; use any target Hermes knows.

   ```bash
   hermes cron create "every 15m" "$(cat prompt.txt)" --name pages-watch \
     --monitor-script pages_feed.py --deliver telegram --catch-up off
   ```

3. Take every tool away from the job. The feed holds text written by strangers, and a job without tools can at worst write a strange message:

   ```bash
   cd /opt/hermes && .venv/bin/python -c '
   import cron.jobs as j
   job = next(x for x in j.list_jobs() if x.get("name") == "pages-watch")
   print(j.update_job(job["id"], {"enabled_toolsets": []})["enabled_toolsets"])'
   ```

   It should print `[]`. An empty list means no tools at all, and no MCP servers either.

4. Try it: publish a page with a password field, then `hermes cron run <job id>`. The first run sees every site; after that, only changes.

The model answers `[SILENT]` when nothing needs you, and Hermes then sends nothing.
