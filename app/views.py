"""The two pages people read: the start page anyone sees, and the dashboard an admin gets a link to.

Plain HTML and CSS. The one script remembers the theme a visitor picked, and the Content-Security-Policy allows that
script by its hash and nothing else. Everything that comes from a published site (names, titles, text) is escaped.
"""
import base64
import hashlib
import html
import urllib.parse
from datetime import datetime, timezone

e = html.escape

CSS = """
/* The theme: the system's unless the visitor picked one in the switcher at the top right. */
:root{--bg:#fafaf8;--fg:#1c1e22;--muted:#5f646b;--line:#e2e2dd;--card:#fff;--accent:#2563a8;--warn:#a85a00;--bar:#2a78d6;--line2:#eb6834;--zero:#e4e4df;--hover:rgba(0,0,0,.05);color-scheme:light}
@media(prefers-color-scheme:dark){:root:not(:has(#t-light:checked)){--bg:#131518;--fg:#e7e7e5;--muted:#9a9fa6;--line:#2b2f35;--card:#1a1d21;--accent:#7fb2ee;--warn:#f0a65a;--bar:#3987e5;--line2:#d95926;--zero:#30353b;--hover:rgba(255,255,255,.06);color-scheme:dark}}
:root:has(#t-dark:checked){--bg:#131518;--fg:#e7e7e5;--muted:#9a9fa6;--line:#2b2f35;--card:#1a1d21;--accent:#7fb2ee;--warn:#f0a65a;--bar:#3987e5;--line2:#d95926;--zero:#30353b;--hover:rgba(255,255,255,.06);color-scheme:dark}
.theme{position:absolute;top:1rem;right:1rem;display:flex;gap:2px;padding:3px;border:1px solid var(--line);border-radius:99px;background:var(--card);margin:0}
.theme input{position:absolute;opacity:0;pointer-events:none}
.theme label{display:grid;place-items:center;width:2rem;height:1.75rem;border-radius:99px;color:var(--muted);cursor:pointer}
.theme label:hover{color:var(--fg)}
.theme input:checked+label{background:var(--bg);color:var(--fg);box-shadow:0 0 0 1px var(--line)}
.theme input:focus-visible+label{outline:2px solid var(--accent);outline-offset:1px}
.theme svg{width:1rem;height:1rem;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
*{box-sizing:border-box}
html{background:var(--bg);color:var(--fg)}
body{margin:0;font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:66rem;margin:0 auto;padding:3.5rem 1.5rem 4rem}
main.wide{max-width:76rem}
h1{font-size:2rem;line-height:1.2;margin:0 0 .4rem}
h2{font-size:1.1rem;margin:2.4rem 0 .7rem}
p{margin:.5rem 0}
a{color:var(--accent)}
.lead{font-size:1.15rem;color:var(--muted);margin:0 0 1rem;max-width:44rem}
p,li{max-width:52rem}
.pill{display:inline-block;font-size:.85rem;padding:.15rem .6rem;border-radius:99px;border:1px solid var(--line);background:var(--card)}
.pill.open{border-color:var(--warn);color:var(--warn)}
.nums{display:grid;grid-template-columns:repeat(auto-fit,minmax(7.5rem,1fr));gap:.6rem;margin:.8rem 0}
.num{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.75rem .9rem}
.num b{display:block;font-size:1.6rem;line-height:1.2;font-variant-numeric:tabular-nums}
.num span{color:var(--muted);font-size:.85rem}
.chart{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.8rem .9rem .5rem}
.chart svg{display:block;width:100%;height:72px}
.chart svg.tall{height:150px}
.chart rect{fill:var(--bar)}
.chart rect.z{fill:var(--zero)}
.chart rect.hit{fill:transparent}
.chart rect.hit:hover{fill:var(--hover)}
.chart polyline{fill:none;stroke:var(--line2);stroke-width:2;stroke-linejoin:round;stroke-linecap:round;pointer-events:none}
.legend{display:flex;flex-wrap:wrap;gap:.3rem 1.1rem;font-size:.82rem;color:var(--muted);margin:0 0 .5rem}
.legend i{display:inline-block;width:.75rem;height:.75rem;border-radius:3px;background:var(--bar);margin-right:.35rem;vertical-align:-.08rem}
.legend i.l{height:3px;border-radius:2px;background:var(--line2);vertical-align:.2rem}
.legend .top{margin-left:auto}
.axis{display:flex;justify-content:space-between;gap:.6rem;color:var(--muted);font-size:.78rem;margin-top:.3rem}
.axis span{white-space:nowrap}
.axis span:nth-child(2){white-space:normal;text-align:center}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.75rem .9rem;overflow-x:auto;font-size:.85rem;margin:.5rem 0}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em}
ul{padding-left:1.2rem;margin:.4rem 0}
li{margin:.3rem 0}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:0 2.5rem;align-items:start}
.cols>div>h2:first-child{margin-top:2.4rem}
@media(max-width:48rem){.cols{grid-template-columns:1fr}}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{text-align:left;padding:.5rem .7rem;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
th{color:var(--muted);font-weight:600;white-space:nowrap}
.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.sub{color:var(--muted);font-size:.82rem}
.warn{color:var(--warn)}
.note{border-left:3px solid var(--warn);padding:.2rem .8rem;margin:1rem 0;color:var(--muted)}
footer{margin-top:3rem;color:var(--muted);font-size:.85rem}
"""


ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#2563a8"/>'
        '<path d="M15 9h23l11 11v35H15z" fill="#fff"/><path d="M38 9v11h11" fill="#cfe0f5"/>'
        '<path d="M23 30h18M23 38h18M23 46h10" stroke="#2563a8" stroke-width="4.5" stroke-linecap="round"/></svg>')
FAVICON = "data:image/svg+xml," + urllib.parse.quote(ICON)

SUN = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
MOON = '<svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
AUTO = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg>'
SWITCH = ('<form class="theme" aria-label="Theme">'
          f'<input type="radio" name="theme" id="t-auto" value="auto" checked><label for="t-auto" title="Like your system">{AUTO}</label>'
          f'<input type="radio" name="theme" id="t-light" value="light"><label for="t-light" title="Light">{SUN}</label>'
          f'<input type="radio" name="theme" id="t-dark" value="dark"><label for="t-dark" title="Dark">{MOON}</label></form>')
# Right after the switcher, so a remembered theme is set before anything below it is drawn.
SCRIPT = ('(function(){var k="agentpages-theme";try{var r=document.getElementById("t-"+localStorage.getItem(k));if(r)r.checked=true}'
          'catch(_){}document.querySelectorAll("input[name=theme]").forEach(function(i){i.addEventListener("change",function(){'
          'try{localStorage.setItem(k,i.value)}catch(_){}})})})();')
SCRIPT_HASH = "sha256-" + base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()


def page(title, body, wide=False):
    return (f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="referrer" content="no-referrer"><title>{e(title)}</title><link rel="icon" href="{FAVICON}">'
            f'<style>{CSS}</style>{SWITCH}<script>{SCRIPT}</script>'
            f'<main{" class=wide" if wide else ""}>{body}</main></html>')


def size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1048576:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1048576:.1f} MB"


def num(value, label):
    return f'<div class="num"><b>{e(str(value))}</b><span>{e(label)}</span></div>'


def bars(days, key, what):
    """A bar per day as inline SVG, with the day and count on hover."""
    values = [d[key] for d in days]
    top = max(values) or 1
    w = 10
    rects = []
    for i, (d, v) in enumerate(zip(days, values)):
        h = max(1.5, 60 * v / top) if v else 1.5
        rects.append(f'<rect x="{i * w + 1}" y="{62 - h:.1f}" width="{w - 2}" height="{h:.1f}" rx="1.5"{" class=z" if not v else ""}>'
                     f'<title>{e(d["day"])}: {v} {e(what)}</title></rect>')
    return (f'<div class="chart"><svg viewBox="0 0 {len(days) * w} 62" preserveAspectRatio="none" role="img" '
            f'aria-label="{e(what)} per day">{"".join(rects)}</svg>'
            f'<div class="axis"><span>{e(days[0]["day"])}</span><span>{sum(values)} {e(what)}, {len(days)} days</span>'
            f'<span>{e(days[-1]["day"])}</span></div></div>')


def traffic(days):
    """Visits per day as bars and visitors per day as a line, one scale for both, as inline SVG. Hovering a day
    shows its numbers; the tables on the dashboard carry the same counts as text."""
    top = max([d["visits"] for d in days] + [d["visitors"] for d in days] + [1])
    w, h = 10, 100
    y = lambda v: h - (h - 2) * v / top
    parts, points = [], []
    for i, d in enumerate(days):
        v, u = d["visits"], d["visitors"]
        if v:
            parts.append(f'<rect x="{i * w + 1.5}" y="{y(v):.1f}" width="{w - 3}" height="{h - y(v):.1f}" rx="1.5"/>')
        else:
            parts.append(f'<rect class=z x="{i * w + 1.5}" y="{h - 1.5}" width="{w - 3}" height="1.5"/>')
        points.append(f"{i * w + w / 2:.1f},{y(u):.1f}")
    hits = "".join(f'<rect class=hit x="{i * w}" y="0" width="{w}" height="{h}"><title>{e(d["day"])}: {d["visits"]} visit'
                   f'{"" if d["visits"] == 1 else "s"}, {d["visitors"]} visitor{"" if d["visitors"] == 1 else "s"}</title></rect>'
                   for i, d in enumerate(days))
    line = f'<polyline points="{" ".join(points)}" vector-effect="non-scaling-stroke"/>' if len(days) > 1 else ""
    visits, visitors = sum(d["visits"] for d in days), sum(d["visitors"] for d in days)
    return (f'<div class="chart"><div class="legend"><span><i></i>Visits (page views)</span><span><i class="l"></i>Visitors</span>'
            f'<span class="top">{visits} visits, {visitors} visitors in {len(days)} days</span></div>'
            f'<svg class="tall" viewBox="0 0 {len(days) * w} {h}" preserveAspectRatio="none" role="img" '
            f'aria-label="Visits and visitors per day, {len(days)} days: {visits} visits, {visitors} visitors">'
            f'{"".join(parts)}{line}{hits}</svg>'
            f'<div class="axis"><span>{e(days[0]["day"])}</span><span>top: {top} a day</span>'
            f'<span>{e(days[-1]["day"])}</span></div></div>')


def when(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def home(c):
    t, lim = c["totals"], c["limits"]
    mcp = c["public_url"] + "/mcp"
    header = '' if c["danger"] else '\n    headers:\n      Authorization: "Bearer &lt;your key&gt;"'
    claude = '' if c["danger"] else ' --header "Authorization: Bearer &lt;your key&gt;"'
    mode = ('<span class="pill open">Anyone can publish here, no key needed</span>' if c["danger"]
            else '<span class="pill">Agents with a key publish here</span>')
    if c["site_domain"]:
        address = (f"<li><b>An address of its own</b> for each site, <code>https://&lt;site&gt;.{e(c['site_domain'])}/</code>, so paths "
                   "from the root (<code>/assets/app.js</code>) and a default Vite build work.</li>")
    else:
        address = ("<li><b>A folder of its own</b> for each site, <code>/&lt;site&gt;/</code>: use relative paths between files. "
                   "In Vite, set <code>base: './'</code>.</li>")
    key = ("<p>No key is needed here: publishing gives back an <code>edit_token</code>, the only way to change that site later.</p>"
           if c["danger"] else
           "<p>A key comes from whoever runs this server. Each agent has its own, so its sites are its own.</p>")
    danger_limits = (f"<tr><td>Without a key</td><td class=n>{lim['danger_mb']} MB a site, {lim['danger_per_hour']} publishes an hour "
                     "per address</td></tr>" if c["danger"] else "")
    tools = "".join(f"<li><code>{e(n)}</code> {e(d)}</li>" for n, d in c["tools"])
    body = f"""
<h1>AgentPages</h1>
<p class="lead">AI agents publish websites here, such as games, apps and landing pages, and get a link anyone can open with no sign-in.</p>
<p>{mode}</p>
<p>Got a link from someone? It goes straight to their site. Nothing here is listed: a site is found only through its link.</p>

<h2>On this server</h2>
<div class="nums">{num(t["sites"], "sites")}{num(t["agents"], "agents publishing")}{num(t["publishes_7d"], "publishes, 7 days")}{num(t["visits_7d"], "visits, 7 days")}{num(t["visitors_7d"], "visitors, 7 days")}{num(size(t["bytes"]), "published")}</div>
{traffic(c["days"])}

<h2>Connect your agent</h2>
<p>AgentPages is an MCP server at <code>{e(mcp)}</code>.</p>
<div class="cols"><div><p>In Hermes, add it to <code>config.yaml</code>:</p>
<pre><code>mcp_servers:
  pages:
    url: "{e(mcp)}"{header}</code></pre></div>
<div><p>In Claude Code:</p>
<pre><code>claude mcp add --transport http pages {e(mcp)}{claude}</code></pre></div></div>
{key}
<p>Then ask it to publish a folder. It gets one command for its terminal, so a whole game never passes through the chat.</p>

<h2>What a site can be</h2>
<div class="cols"><div><p><b>Works</b></p><ul>
<li>HTML, CSS and JavaScript, with images, fonts, sound, video and WebAssembly: any static files, with an <code>index.html</code> at the top.</li>
<li>Games and apps that keep their state in the visitor's browser (<code>localStorage</code>, IndexedDB).</li>
{address}
<li>A <code>404.html</code> of its own.</li>
<li>Undo: the last {lim["versions"]} versions of each site are kept, and <code>restore_site</code> brings one back.</li>
</ul></div><div><p><b>Does not</b></p><ul>
<li>No code on the server and no database: a site runs in the visitor's browser only.</li>
<li>No sign-in and no private sites. Anyone with the link sees every file, so never publish keys, passwords or personal data.</li>
<li>Search engines are asked not to index sites.</li>
</ul></div></div>

<div class="cols"><div><h2>Limits</h2>
<div class="scroll"><table>
<tr><td>Size of a site</td><td class=n>{lim["site_mb"]} MB</td></tr>
<tr><td>Files in a site</td><td class=n>{lim["files"]}</td></tr>
<tr><td>Sites on this server</td><td class=n>{t["sites"]} of {lim["sites"]}</td></tr>
<tr><td>Earlier versions kept</td><td class=n>{lim["versions"]} per site</td></tr>{danger_limits}
</table></div></div>

<div><h2>Tools</h2>
<ul>{tools}</ul>
<p class="sub">Admins also get <code>list_all_sites</code> and <code>dashboard_link</code>.</p></div></div>

<footer>AgentPages {e(c["version"])} · <a href="{e(c["docs"])}" rel="noreferrer">Documentation</a> · Visits count page views in a browser; visitors count people per day, told apart without cookies by a code that is thrown away at midnight. Nothing about a visitor is kept.</footer>"""
    return page("AgentPages", body)


def ref_row(r):
    to = ", ".join(f'{e(site)} <span class=sub>{n}</span>' for site, n in r["sites"][:5])
    if len(r["sites"]) > 5:
        to += f' <span class=sub>and {len(r["sites"]) - 5} more</span>'
    return f'<tr><td>{e(r["domain"])}</td><td class=n>{r["visits_7d"]}</td><td class=n>{r["visits_30d"]}</td><td>{to}</td></tr>'


def dashboard(c):
    t = c["totals"]
    mode = ('<span class="pill open">Open: anyone can publish without a key</span>' if c["danger"]
            else '<span class="pill">Closed: only agents with a key publish</span>')
    agents = "".join(
        f'<tr><td>{e(a["agent"])}</td><td class=n>{a["sites"]}</td><td class=n>{size(a["bytes"])}</td><td class=n>{a["publishes_7d"]}</td>'
        f'<td class=n>{a["publishes_30d"]}</td><td class=n>{a["visits_7d"]}</td><td class=n>{a["visits_30d"]}</td></tr>'
        for a in c["agents"]) or '<tr><td colspan=7 class=sub>No one has published yet.</td></tr>'
    rows = []
    for s in c["sites"]:
        signals = "; ".join(s["signals"])
        title = s["title"] or s["page_title"]
        rows.append(
            f'<tr><td><a href="{e(s["url"])}" target="_blank" rel="noopener noreferrer">{e(s["name"])}</a>'
            f'{f"<div class=sub>{e(title)}</div>" if title else ""}</td>'
            f'<td>{e(s["owner"])}</td><td class=n>v{s["version"]}<div class=sub>{s["kept"]} kept</div></td>'
            f'<td class=n>{size(s["bytes"])}<div class=sub>{s["files"]} file{"" if s["files"] == 1 else "s"}</div></td><td class=n>{e(when(s["updated"]))}</td>'
            f'<td class=n>{s["visits_7d"]}<div class=sub>{s["visits_30d"]} in 30 d</div></td>'
            f'<td class=n>{s["visitors_7d"]}<div class=sub>{s["visitors_30d"]} in 30 d</div></td>'
            f'<td>{f"<span class=warn>{e(signals)}</span>" if signals else "<span class=sub>none</span>"}</td>'
            f'<td><a href="{e(s["takedown"])}" target="_blank" rel="noopener noreferrer">take down</a></td></tr>')
    sites = "".join(rows) or '<tr><td colspan=9 class=sub>No sites.</td></tr>'
    refs = "".join(ref_row(r) for r in c["referrers"][:25])
    refs += (f'<tr><td><i>No other site</i><div class=sub>a link in a message, an app or the address bar</div></td><td class=n>{c["direct_7d"]}</td>'
             f'<td class=n>{c["direct_30d"]}</td><td class=sub>every site</td></tr>')
    settings = "".join(
        f'<tr><td><code>{e(k)}</code></td><td>{e(v)}</td><td class=sub>{e(d)}</td><td>{e(what)}</td></tr>' for k, v, d, what in c["settings"])
    body = f"""
<h1>AgentPages dashboard</h1>
<p class="lead">{e(c["public_url"])} · AgentPages {e(c["version"])}</p>
<p>{mode}</p>
<p class="note">This link was made for {e(c["for"])} and works until {e(c["expires"])} UTC. Anyone who has it sees everything here, so do not pass it on.</p>

<div class="nums">{num(t["sites"], f'sites ({t["anonymous_sites"]} without a key)')}{num(t["agents"], "agents publishing")}{num(t["publishes_7d"], f'publishes, 7 days ({t["publishes_30d"]} in 30)')}{num(t["visits_7d"], f'visits, 7 days ({t["visits_30d"]} in 30)')}{num(t["visitors_7d"], f'visitors, 7 days ({t["visitors_30d"]} in 30)')}{num(size(t["bytes"]), f'live ({size(c["disk"])} on disk with versions)')}</div>

<h2>Visits and visitors per day</h2>
{traffic(c["history"])}

<h2>Where visitors came from</h2>
<p class="sub">The last 30 days, by the site a visitor clicked through from. Only its domain is kept. Many apps (LinkedIn, Slack, mail) send none, so their visits land under "No other site".</p>
<div class="scroll"><table>
<tr><th>From</th><th class=n>Visits 7 d</th><th class=n>30 d</th><th>To</th></tr>
{refs}
</table></div>
<h2>Publishes per day</h2>
{bars(c["days"], "publishes", "publishes")}

<h2>Agents</h2>
<div class="scroll"><table>
<tr><th>Agent</th><th class=n>Sites</th><th class=n>Size</th><th class=n>Publishes 7 d</th><th class=n>30 d</th><th class=n>Visits 7 d</th><th class=n>30 d</th></tr>
{agents}
</table></div>

<h2>Every site</h2>
<p class="sub">Newest first. Site content was written by whoever published it: open it with care. A take-down link asks once more before anything happens.</p>
<div class="scroll"><table>
<tr><th>Site</th><th>Owner</th><th class=n>Version</th><th class=n>Size</th><th class=n>Updated (UTC)</th><th class=n>Visits 7 d</th><th class=n>Visitors 7 d</th><th>Looks risky</th><th></th></tr>
{sites}
</table></div>

<h2>Settings</h2>
<p>These come from the server's environment. To change one, set the variable and restart the server. In Agent Hotel, open the guest's Settings, change the value and save: the guest redeploys with it.</p>
<div class="scroll"><table>
<tr><th>Variable</th><th>Now</th><th>Default</th><th>What it does</th></tr>
{settings}
</table></div>

<footer>Visits count page views in a browser; prefetches, link previews and bots are left out. Visitors count people per day, told apart by a salted hash of address and browser that is thrown away at midnight UTC, so the 7- and 30-day figures add days up and someone who comes back on another day counts again. Nothing about a visitor is kept. <a href="{e(c["docs"])}" rel="noreferrer">Documentation</a></footer>"""
    return page("AgentPages dashboard", body, wide=True)
