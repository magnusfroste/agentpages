# AgentPages

Your agent publishes a game, a page or an app, and gets a link anyone can open. No account, no sign-in, no build step.

```text
You:    Publish the game in /opt/data/my-game and send me the link.
Agent:  publish_folder(folder="/opt/data/my-game")
        tar -czf - -C /opt/data/my-game . | curl -sS --data-binary @- https://pages.example.com/upload/...
        https://pages.example.com/witty-puffin-dd6e1f/
```

The agent runs that one command in its terminal and has a link to share. Its key stays in the MCP connection, and the files never pass through the conversation, so a 400 KB game costs no more tokens than a one-liner. Publish again with the same `site` and the link stays the same while visitors get the new version.

It is one small server for many agents, like a shared drive: each agent publishes with its own key and only changes its own sites. Python standard library only, no database.

## Run it

```bash
cp .env.example .env   # set MCP_KEYS and PUBLIC_URL
docker compose up -d
```

Then connect each agent to `https://<domain>/mcp` with the header `Authorization: Bearer <its key>`. For Hermes, in `config.yaml`:

```yaml
mcp_servers:
  pages:
    url: https://pages.example.com/mcp
    headers:
      Authorization: Bearer <key>
      Accept: "application/json, text/event-stream"
```

On [AgentHotel](https://github.com/magnusfroste/agenthotel), deploy the **Pages** template instead: it generates the first key and makes that agent the admin.

| Variable | Meaning | Default |
|---|---|---|
| `MCP_KEYS` | `name:key` pairs, comma separated, one per agent. Keys are 32+ characters (`openssl rand -hex 32`) | none |
| `PUBLIC_URL` | The address of this server, for the links agents get back | none |
| `ADMIN_AGENTS` | Names from `MCP_KEYS` that can list and take down every site | none |
| `MAX_SITE_MB` | Size limit per site | 25 |
| `MAX_FILES` | Files per site | 500 |
| `MAX_SITES` | Sites on this server | 500 |
| `DANGER_MODE` | `on` lets anyone publish without a key. See [Danger mode](#danger-mode) | off |
| `DANGER_MAX_SITE_MB` | Size limit per site published without a key | 5 |
| `DANGER_PUBLISHES_PER_HOUR` | Publishes per hour from one address without a key | 20 |
| `TRUSTED_PROXY_HEADER` | The header your proxy puts the visitor's address in, e.g. `X-Real-IP` | none |

## Tools

| Tool | Who | What it does |
|---|---|---|
| `publish_folder` | every agent | One command that publishes a folder from disk and prints the link. The upload link works once, for 15 minutes |
| `publish_site` | every agent | Publish `html` for one page, or `files` and `binary_files` for a few. Best for small sites |
| `list_sites` | every agent | The sites this agent has published |
| `read_site` | every agent | A site's file list, or one file, to change something published earlier |
| `delete_site` | every agent | Take down one of its own sites. An admin can take down any site |
| `list_all_sites` | admin only | Every site, newest first, with who published it and warning signs |

Hidden files and `node_modules` are left out of an upload. A site needs an `index.html` at the top, and may have its own `404.html`.

## Admin and moderation

Agents named in `ADMIN_AGENTS` see `list_all_sites`, and `delete_site` works on every site for them. Other agents never see what anyone else has published.

With an admin key, `GET /moderation/feed` returns every site as plain text:

```text
site eager-willow-1ef8cc | anonymous | updated 2026-11-09 21:57 UTC | 1 files, 0 KB
  url: https://pages.example.com/eager-willow-1ef8cc/
  title: Log in - Examplebank / given: test
  text: Your account is locked. Log in to unlock it.
  signals: password field; form sends to collect.example.net; redirects the visitor
  external hosts: collect.example.net
  take down: https://pages.example.com/moderation/takedown/eager-willow-1ef8cc/...
```

The signals are password fields, forms that send elsewhere, redirects, obfuscation-like calls, downloadable programs and external hosts. The feed has no clock in it, so a watcher can compare it with the last one and only look when something changed.

Each take-down link opens a page with a button. A chat app that previews the link cannot take the site down, and the link stops working when the site changes.

### Let a Hermes watch it

[`examples/hermes`](examples/hermes) has a monitor script and a prompt. Hermes runs the script every 15 minutes and only wakes the model when the feed changed. It then sends you a message about anything that looks like phishing, malware or spam, with the take-down link. It takes nothing down by itself.

Run the job with no tools: site content is written by strangers and should only reach a model as text to judge, never next to tools it could be talked into using. The README in that folder shows how.

## Danger mode

With `DANGER_MODE=on`, anyone who knows the address of `/mcp` can publish, with no key. Keyed agents work as before.

- The first publish without a key returns an `edit_token`. It is the only way to update or delete that site, and it cannot be recovered.
- Nobody can change a site without its key or `edit_token`, so an agent's sites stay safe.
- Publishing without a key has its own size limit and a limit per address and hour.
- The start page says that anyone can publish.

An open host fills up with whatever people put there, and phishing pages on it can get the whole domain blocked. Watch it, and turn it on only where you can live with that.

## What to know before you share it

- **Sites are public to anyone with the link.** They are not listed anywhere and ask search engines not to index them, but the link is the only protection.
- **Sites are static.** JavaScript runs in the visitor's browser. There is no server code or database.
- **Links between files must be relative.** A site lives under its own folder, so `/assets/app.js` does not load. `publish_site` warns about this. In Vite, set `base: './'`.
- **Sites share one origin.** `localStorage` works, which games need, and one site can read what another stored there. Nothing secret lives on this origin: no cookies, no sign-ins, and the keys only travel in the `Authorization` header of MCP calls. Do not publish a site that keeps private data in the browser.
- **Files live in `/data/sites/<name>/`.** Back up that volume. To remove a site by hand, delete its folder.

## Test

```bash
python -m unittest discover -s tests
```

The tests start the real server on a free port and use it the way agents and visitors do.

## Licence

MIT
