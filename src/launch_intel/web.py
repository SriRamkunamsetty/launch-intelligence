"""Read-only dashboard server (stdlib only): JSON API + single-page UI.
Security posture: binds 127.0.0.1 by default, no auth (put it behind your own auth proxy before exposing it),
CSP + nosniff headers, request bodies capped, all user-derived text inserted with textContent (no innerHTML)."""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .db import parse_ts
from .metrics import anomalies, build_facts, cumulative_series, title_posts
from .narrator import fmt, ignore_list, render, template_narrative, verify

MAX_BODY = 20_000
HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
           "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:"}


def _json(obj, status=200):
    return status, "application/json", json.dumps(obj, default=str).encode()

class App:
    def __init__(self, conn):
        self.conn, self.lock = conn, threading.Lock()

    def meta(self):
        return {"sample_rows": int(self.conn.execute("SELECT COUNT(*) c FROM posts WHERE is_sample=1").fetchone()["c"])}

    def _ids(self):
        return [r["id"] for r in self.conn.execute("SELECT id FROM titles ORDER BY id")]

    def titles(self):
        out = []
        for t in self.conn.execute("SELECT id, name, genre FROM titles ORDER BY id").fetchall():
            f = {x.id: x for x in build_facts(self.conn, t["id"], [])}
            out.append({"id": t["id"], "name": t["name"], "genre": t["genre"], "posts": len(title_posts(self.conn, t["id"])),
                        "velocity_72h": fmt(f["velocity_72h"]) if "velocity_72h" in f else None})
        return out

    def title(self, tid):
        row = self.conn.execute("SELECT id, name, genre FROM titles WHERE id=?", (tid,)).fetchone()
        if not row:
            return None
        peers = [i for i in self._ids() if i != tid]
        facts = build_facts(self.conn, tid, peers)
        text = render(template_narrative(row["name"], facts, self.conn, tid), facts)
        rep = verify(text, facts, ignore=ignore_list(self.conn, tid, row["name"]))
        series = cumulative_series(self.conn, tid)
        start = min((parse_ts(r["published_at"]) for r in self.conn.execute("SELECT published_at FROM posts WHERE title_id=?", (tid,))), default=None)
        events = [{"hours": (parse_ts(e["occurred_at"]) - start).total_seconds() / 3600, "label": e["label"], "kind": e["kind"]}
                  for e in self.conn.execute("SELECT occurred_at, kind, label FROM events WHERE title_id=? ORDER BY occurred_at", (tid,))] if start else []
        return {"id": tid, "name": row["name"], "genre": row["genre"], "narrative": text,
                "facts": [{"id": f.id, "label": f.label, "value": f.value, "text": fmt(f), "query": f.query} for f in facts],
                "verification": {"ok": rep.ok, "numbers": rep.n_numbers, "claim_accuracy": rep.claim_accuracy, "problems": rep.reasons()},
                "series": series, "events": events,
                "anomalies": [{"day": a["day_index"], "views": a["views"], "direction": a["direction"], "events": a["events"]} for a in anomalies(self.conn, tid)]}

    def route(self, method, raw_path, body):
        if method != "GET":
            return _json({"error": "method not allowed"}, 405)
        p = urlsplit(raw_path).path
        with self.lock:
            if p == "/":
                return 200, "text/html; charset=utf-8", PAGE.encode()
            if p == "/healthz":
                return _json({"ok": True})
            if p == "/api/meta":
                return _json(self.meta())
            if p == "/api/titles":
                return _json(self.titles())
            m = re.fullmatch(r"/api/title/(\d+)", p)
            if m:
                t = self.title(int(m[1]))
                return _json(t) if t else _json({"error": "not found"}, 404)
        return _json({"error": "not found"}, 404)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def _run(self, method):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    status, ctype, body = _json({"error": "payload too large"}, 413)
                else:
                    status, ctype, body = app.route(method, self.path, self.rfile.read(n) if n else b"")
            except Exception as exc:  # never leak a stack trace to the client
                print(f"error on {self.path}: {exc!r}", file=sys.stderr)
                status, ctype, body = _json({"error": "internal error"}, 500)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._run("GET")

        def do_POST(self):
            self._run("POST")

        def log_message(self, fmt_, *args):
            pass
    return Handler


def serve(conn, host="127.0.0.1", port=8000):
    srv = ThreadingHTTPServer((host, port), make_handler(App(conn)))
    print(f"dashboard on http://{host}:{port}  (read-only, no auth)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.server_close()


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Launch Intelligence - Launch Desk</title><style>
:root{--paper:#f2ead8;--panel:#ebe2cb;--ink:#1c1a16;--muted:#6b6455;--rule:#cfc4a8;--accent:#c2410c;--cool:#1f4e5f;--good:#2f6b3a;--bad:#a4261b;
--serif:"Iowan Old Style","Palatino Linotype","Book Antiqua",Georgia,serif;--mono:ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--paper:#15130f;--panel:#1d1a14;--ink:#ece3cc;--muted:#a39a84;--rule:#3a3528;--accent:#f08a4b;--cool:#7fc0d4;--good:#7fc98a;--bad:#f08478}}
*{box-sizing:border-box}html,body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 var(--serif)}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.banner{background:var(--accent);color:var(--paper);font:600 12px var(--mono);letter-spacing:.08em;text-transform:uppercase;padding:6px 16px;text-align:center}
.wrap{display:grid;grid-template-columns:270px 1fr;min-height:100vh}
aside{border-right:1px solid var(--rule);padding:22px 0}
aside h2{font:600 11px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:0 20px 10px}
.slate button{all:unset;display:flex;justify-content:space-between;gap:8px;width:100%;box-sizing:border-box;padding:9px 20px;cursor:pointer;border-left:3px solid transparent;border-bottom:1px solid var(--rule)}
.slate button:hover{background:var(--panel)}.slate button[aria-current=true]{border-left-color:var(--accent);background:var(--panel)}
.slate small{display:block;color:var(--muted);font:11px var(--mono);text-transform:uppercase;letter-spacing:.08em}
main{padding:26px 34px 60px;min-width:0}
.kicker{font:600 11px var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--accent)}
h1{font:700 clamp(30px,4.4vw,52px)/1.02 var(--serif);letter-spacing:-.02em;margin:6px 0 18px}
.figs{display:grid;grid-template-columns:repeat(4,1fr);border-top:2px solid var(--ink);border-bottom:1px solid var(--rule);margin-bottom:22px}
.fig{padding:12px 16px;border-right:1px solid var(--rule)}.fig:last-child{border-right:0}
.fig b{display:block;font:600 clamp(20px,2.6vw,32px) var(--mono)}.fig span{font:11px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
nav.tabs{display:flex;gap:22px;border-bottom:1px solid var(--rule);margin-bottom:22px}
nav.tabs button{all:unset;cursor:pointer;padding:8px 0;font:600 12px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);border-bottom:2px solid transparent}
nav.tabs button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--accent)}
.read{font-size:19px;max-width:64ch;margin:0 0 10px}
.badge{display:inline-block;font:600 11px var(--mono);letter-spacing:.06em;padding:3px 8px;border:1px solid currentColor;margin-bottom:18px}.ok{color:var(--good)}.fail{color:var(--bad)}
h3{font:600 11px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:26px 0 8px;border-bottom:1px solid var(--rule);padding-bottom:6px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:7px 10px 7px 0;border-bottom:1px solid var(--rule);vertical-align:top}
th{font:600 11px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}td.n,th.n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;padding-left:18px}
.dim{color:var(--muted)}.chip{font:600 11px var(--mono);padding:2px 6px;border:1px solid currentColor;text-transform:uppercase}
.bar{display:inline-block;height:8px;background:var(--cool);vertical-align:middle}.bar.neg{background:var(--accent)}
.call{border-left:3px solid var(--accent);background:var(--panel);padding:10px 14px;margin:8px 0}
svg text{font:11px var(--mono);fill:var(--muted)}.scroll{overflow-x:auto}
@media (max-width:820px){.wrap{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid var(--rule);padding:12px 0 0}.slate{display:flex;overflow-x:auto}.slate button{flex:0 0 auto;width:auto;border-left:0;border-bottom:3px solid transparent}.slate button[aria-current=true]{border-bottom-color:var(--accent)}.figs{grid-template-columns:1fr 1fr}main{padding:20px 16px}}

.field{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin:0 0 18px}
select,textarea,button.act{font:14px var(--serif);color:var(--ink);background:var(--panel);border:1px solid var(--rule);padding:6px 10px}
textarea{width:100%;min-height:110px;resize:vertical;line-height:1.5}
button.act{cursor:pointer;font:600 12px var(--mono);letter-spacing:.08em;text-transform:uppercase;background:var(--ink);color:var(--paper);border-color:var(--ink)}
label.f{font:600 11px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);display:flex;flex-direction:column;gap:4px}
mark{background:none;color:inherit;padding:0 3px;border-bottom:2px solid}
mark.ok{border-color:var(--good)}mark.unsupported{border-color:var(--bad);background:color-mix(in srgb,var(--bad) 14%,transparent)}
mark.misattributed{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 16%,transparent)}mark.style{border-color:var(--muted);border-bottom-style:dashed}
.legend{display:flex;gap:16px;flex-wrap:wrap;font:11px var(--mono);color:var(--muted);margin:8px 0 0}
.two{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:28px}@media (max-width:980px){.two{grid-template-columns:1fr}}
.pill{font:600 10px var(--mono);letter-spacing:.08em;text-transform:uppercase;padding:2px 6px;border:1px solid currentColor;margin-left:6px}
tr.sel td{background:var(--panel)}tr.click{cursor:pointer}tr.click:hover td{background:var(--panel)}
.wrap.single{grid-template-columns:1fr}
@media (max-width:820px){.wrap,.wrap.single{grid-template-columns:minmax(0,1fr)}aside,main{min-width:0}.slate{max-width:100vw}.figs{grid-template-columns:1fr 1fr}.fig{border-right:0;border-bottom:1px solid var(--rule)}}main{min-width:0}svg{max-width:100%;height:auto}@media (max-width:820px){th,td{padding:6px 6px 6px 0;font-size:13px}td.n,th.n{padding-left:6px}.bar2{max-width:44px}.chip{font-size:10px}}button.act.off{background:var(--panel);color:var(--ink);border-color:var(--rule)}.bar2{display:inline-block;height:10px;background:var(--cool);vertical-align:middle}.bar2.warn{background:var(--accent)}</style></head><body><div id="banner" class="banner" hidden>Synthetic demo data. Not real platform data.</div><div class="wrap"><aside><h2>The slate</h2><div id="slate" class="slate"></div></aside><main><div class="kicker">Launch desk</div><h1 id="title">Loading</h1><div id="figs" class="figs"></div><section id="panel"></section></main></div><script>
"use strict";
const $=s=>document.querySelector(s);
function h(tag,attrs,...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs||{})){if(k==="class")e.className=v;else if(k.startsWith("on"))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v)}
 for(const c of kids.flat(Infinity)){if(c==null||c===false)continue;e.append(c.nodeType?c:document.createTextNode(String(c)))}return e}
function sv(tag,attrs,...kids){const e=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const[k,v]of Object.entries(attrs||{}))e.setAttribute(k,v);for(const c of kids.flat(Infinity))if(c!=null)e.append(typeof c==="string"?document.createTextNode(c):c);return e}
const api=async p=>{const r=await fetch(p);if(!r.ok)throw new Error(p+" "+r.status);return r.json()};
const pct=(x,d=0)=>x==null?"n/a":(x*100).toFixed(d)+"%";const big=v=>v>=1e6?(v/1e6).toFixed(2)+"M":v>=1e3?(v/1e3).toFixed(0)+"K":Math.round(v)+"";

function chart(d){const W=760,H=260,L=54,B=28,T=14,R=14;const s=d.series;if(s.length<2)return h("p",{class:"dim"},"Not enough snapshots to chart yet.");
 const xmax=s[s.length-1][0],ymax=Math.max(...s.map(p=>p[1]))*1.05;const X=x=>L+(x/xmax)*(W-L-R),Y=y=>H-B-(y/ymax)*(H-B-T);
 const g=sv("svg",{viewBox:`0 0 ${W} ${H}`,width:"100%",role:"img","aria-label":"Cumulative views over time with logged events"});
 for(let i=0;i<=4;i++){const y=ymax*i/4;g.append(sv("line",{x1:L,x2:W-R,y1:Y(y),y2:Y(y),stroke:"var(--rule)"}),sv("text",{x:L-6,y:Y(y)+4,"text-anchor":"end"},big(y)))}
 for(let dday=0;dday<=xmax/24;dday+=Math.max(1,Math.round(xmax/24/7)))g.append(sv("text",{x:X(dday*24),y:H-8,"text-anchor":"middle"},"d"+dday));
 d.events.filter(e=>e.hours>=0&&e.hours<=xmax).forEach((e,i)=>{const x=X(e.hours);g.append(sv("line",{x1:x,x2:x,y1:T,y2:H-B,stroke:"var(--accent)","stroke-dasharray":"3 3"}),sv("text",{x:x+4,y:T+11+i*12,style:"fill:var(--accent)"},e.label))});
 g.append(sv("polyline",{fill:"none",stroke:"var(--ink)","stroke-width":2,points:s.map(p=>X(p[0])+","+Y(p[1])).join(" ")}));return h("div",{class:"scroll"},g)}
function launch(d){const v=d.verification;return h("div",{},
 h("p",{class:"read"},d.narrative),
 h("span",{class:"badge "+(v.ok?"ok":"fail")},v.ok?`CLAIMS CHECKED: ${v.numbers} numbers traced to facts`:"CLAIM CHECK FAILED"),
 v.problems.map(p=>h("div",{class:"call"},p)),
 h("h3",{},"Cumulative views, all posts"),chart(d),
 d.anomalies.length?[h("h3",{},"Unusual episodes"),d.anomalies.map(a=>h("div",{class:"call"},`Day ${a.day}, ${a.direction}: ${big(a.views)} views. `,a.events.length?"Logged near it: "+a.events.join("; ")+". Timing only, not proof of cause.":"No logged event nearby."))]:null,
 h("h3",{},"Fact ledger (every number the narrative may cite)"),
 h("div",{class:"scroll"},h("table",{},h("thead",{},h("tr",{},h("th",{},"Fact"),h("th",{class:"n"},"Value"),h("th",{},"How it was computed"))),
 h("tbody",{},d.facts.map(f=>h("tr",{},h("td",{},f.label),h("td",{class:"n"},f.text),h("td",{class:"dim"},f.query)))))))}

const state={titles:[],cur:null};
async function pick(t){state.cur=t;$("#title").textContent=t.name;document.querySelectorAll("#slate button").forEach(b=>b.setAttribute("aria-current",b.dataset.id==t.id));
 const p=$("#panel");p.replaceChildren(h("p",{class:"dim"},"Loading"));
 try{const d=await api("/api/title/"+t.id),F=Object.fromEntries(d.facts.map(f=>[f.id,f.text]));
 $("#figs").replaceChildren(...[["72h views",F.velocity_72h],["Engagement",F.eng_rate],["Share of voice",F.sov_7d],["Genre percentile",F.benchmark_72h]].map(([l,v])=>h("div",{class:"fig"},h("b",{},v||"n/a"),h("span",{},l))));
 p.replaceChildren(launch(d))}catch(e){p.replaceChildren(h("div",{class:"call fail"},"Could not load: "+e.message))}}
(async()=>{const m=await api("/api/meta");if(m.sample_rows)$("#banner").hidden=false;state.titles=await api("/api/titles");
 $("#slate").replaceChildren(...state.titles.map(t=>h("button",{"data-id":t.id,onclick:()=>pick(t)},h("span",{},t.name,h("small",{},t.genre||"")),h("span",{class:"mono dim"},t.velocity_72h||""))));
 if(state.titles.length)pick(state.titles[0]);else{$("#title").textContent="No data yet";$("#panel").replaceChildren(h("p",{class:"dim"},"Run `launch-intel collect` or `launch-intel ingest-csv`, or start with --demo."))}})();

</script></body></html>"""
