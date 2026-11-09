# AgentPages

An agent publishes HTML and JavaScript and gets a link anyone can open. No account, no sign-in, no build step.

```text
publish_folder(folder="/opt/data/my-game")
-> tar -czf - -C /opt/data/my-game . | curl -sS --data-binary @- https://pages.example.com/upload/...
-> https://pages.example.com/witty-puffin-dd6e1f/
```

The agent runs that one command in its terminal and has a link to send. The key stays in its MCP connection, and the files never pass through the conversation. For a single page, `publish_site(html="<h1>Hello</h1>")` is enough.

Publish again with the same `site` and the link stays the same while visitors get the new version.

## Run it

```bash
cp .env.example .env   # set MCP_KEYS and PUBLIC_URL
docker compose up -d
```

Connect an agent to `https://<domain>/mcp` with `Authorization: Bearer <key>`. Python standard library only, no database.

| Variable | Meaning | Default |
|---|---|---|
| `MCP_KEYS` | `name:key` pairs, comma separated, one per agent. Keys are 32+ characters | none |
| `PUBLIC_URL` | The address of this server, for the links agents get back | none |
| `MAX_SITE_MB` | Size limit per site | 25 |
| `MAX_FILES` | Files per site | 500 |
| `MAX_SITES` | Sites on this server | 500 |
| `DANGER_MODE` | `on` lets anyone publish without a key. See below | off |
| `DANGER_MAX_SITE_MB` | Size limit per site published without a key | 5 |
| `DANGER_PUBLISHES_PER_HOUR` | Publishes per hour from one address without a key | 20 |
| `TRUSTED_PROXY_HEADER` | The header your proxy puts the visitor's address in, e.g. `X-Real-IP` | none |

## Tools

| Tool | What it does |
|---|---|
| `publish_folder` | One command to publish a folder from disk. Works once, for 15 minutes. Pass `site` to update |
| `publish_site` | Publish `html` for one page, or `files` and `binary_files` for several. Pass `site` to update |
| `list_sites` | The sites this agent has published |
| `read_site` | A site's file list, or one file |
| `delete_site` | Take a site down |

Only the agent that published a site can change or delete it.

## Danger mode

With `DANGER_MODE=on`, anyone who knows the address of `/mcp` can publish, with no key. Keyed agents work as before.

- The first publish without a key returns an `edit_token`. It is the only way to update or delete that site, and it cannot be recovered.
- Nobody can change a site they do not hold the key or `edit_token` for, so an agent's sites stay safe.
- Publishing without a key has its own size limit and a limit per address and hour.

Turn it on for a workshop or a demo, not on a domain you care about. An open host fills up with whatever people put there, and phishing pages on it can get the whole domain blocked.

## What to know before you share it

- **Sites are public to anyone with the link.** They are not listed anywhere and ask search engines not to index them, but the link is the only protection.
- **Sites are static.** JavaScript runs in the visitor's browser. There is no server code or database.
- **Links between files must be relative.** A site lives under its own folder, so `/assets/app.js` does not load. `publish_site` warns about this. In Vite, set `base: './'`.
- **Sites share one origin.** `localStorage` works, which games need, and one site can read what another stored there. Nothing secret lives on this origin: there are no cookies or sign-ins, and publishing takes a key a page never sees. Do not publish a site that stores private data in the browser.
- **To take down someone else's site,** remove its folder: `rm -r /data/sites/<name>`.

## Test

```bash
python -m unittest discover -s tests
```

## Licence

MIT
