#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local browser GUI for Binance Tax FR."""

import argparse
import json
import logging
import os
import queue
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

from binance_tax import BASE_DIR, RAPPORT_FILE, run_report


APP_TITLE = "Binance Tax FR"
HOST = "127.0.0.1"

STATE = {
    "running": False,
    "status": "idle",
    "logs": ["$ ready. Les cles restent locales sur cette machine."],
    "report": str(RAPPORT_FILE),
    "started_at": None,
}
STATE_LOCK = threading.Lock()
SHUTDOWN_EVENT = threading.Event()


class QueueLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        append_log(self.format(record))


def append_log(line: str) -> None:
    with STATE_LOCK:
        STATE["logs"].append(line)
        STATE["logs"] = STATE["logs"][-600:]


def load_existing_env() -> dict:
    load_dotenv(BASE_DIR / ".env")
    key = os.getenv("BINANCE_API_KEY", "")
    secret = os.getenv("BINANCE_API_SECRET", "")
    return {
        "apiKey": "" if key == "ta_cle_ici" else key,
        "apiSecret": "" if secret == "ton_secret_ici" else secret,
    }


def save_env(api_key: str, api_secret: str) -> None:
    (BASE_DIR / ".env").write_text(
        f"BINANCE_API_KEY={api_key.strip()}\nBINANCE_API_SECRET={api_secret.strip()}\n",
        encoding="utf-8",
    )


def run_worker(api_key: str, api_secret: str, should_save: bool) -> None:
    with STATE_LOCK:
        STATE["running"] = True
        STATE["status"] = "running"
        STATE["started_at"] = time.time()
        STATE["report"] = str(RAPPORT_FILE)
    append_log("$ run --tax-year 2025")

    try:
        if should_save:
            save_env(api_key, api_secret)
            append_log("[OK] .env local mis a jour")
        report = run_report(api_key, api_secret)
        with STATE_LOCK:
            STATE["status"] = "done"
            STATE["report"] = str(report)
        append_log(f"[OK] Rapport genere : {report}")
    except Exception as exc:
        with STATE_LOCK:
            STATE["status"] = "failed"
        append_log(f"[ERROR] {exc}")
    finally:
        with STATE_LOCK:
            STATE["running"] = False


def json_response(handler: BaseHTTPRequestHandler, data: dict, status: int = 200) -> None:
    payload = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def text_response(handler: BaseHTTPRequestHandler, text: str, status: int = 200, content_type: str = "text/html") -> None:
    payload = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", f"{content_type}; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "BinanceTaxFR/1.0"

    def log_message(self, _format: str, *_args) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            text_response(self, render_html())
            return
        if path == "/api/config":
            json_response(self, load_existing_env())
            return
        if path == "/api/status":
            with STATE_LOCK:
                json_response(self, dict(STATE))
            return
        if path == "/api/open-report":
            report = Path(str(STATE.get("report") or RAPPORT_FILE))
            if report.exists():
                webbrowser.open(report.as_uri())
                json_response(self, {"ok": True})
            else:
                json_response(self, {"ok": False, "error": "Rapport introuvable"}, 404)
            return
        if path == "/api/quit":
            json_response(self, {"ok": True})
            SHUTDOWN_EVENT.set()
            return
        text_response(self, "Not found", 404, "text/plain")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/run":
            text_response(self, "Not found", 404, "text/plain")
            return

        size = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(size).decode("utf-8") or "{}")
        api_key = str(data.get("apiKey", "")).strip()
        api_secret = str(data.get("apiSecret", "")).strip()
        should_save = bool(data.get("saveEnv", True))

        if not api_key or not api_secret:
            json_response(self, {"ok": False, "error": "API Key et Secret requis"}, 400)
            return
        with STATE_LOCK:
            if STATE["running"]:
                json_response(self, {"ok": False, "error": "Calcul deja en cours"}, 409)
                return

        threading.Thread(target=run_worker, args=(api_key, api_secret, should_save), daemon=True).start()
        json_response(self, {"ok": True})


def render_html() -> str:
    return """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Binance Tax FR</title>
<style>
:root {
  --green-primary:#00FF41; --green-dim:#00CC33; --cyan-accent:#00D4FF;
  --yellow-accent:#FFD700; --red-accent:#FF4444; --bg-primary:#000;
  --bg-secondary:#0A0E18; --bg-terminal:#080D16; --bg-terminal-header:#141E2A;
  --text-primary:#D8E2EC; --text-secondary:#8899AA; --text-heading:#fff;
  --border-default:#1E3044; --border-green:rgba(0,255,65,.2);
  --radius-md:6px; --radius-lg:8px;
}
* { box-sizing:border-box; }
body {
  margin:0; min-height:100vh; color:var(--text-primary); background:var(--bg-primary);
  font-family:"JetBrains Mono", Consolas, ui-monospace, monospace;
}
body:after {
  content:""; position:fixed; inset:0; pointer-events:none;
  background:repeating-linear-gradient(0deg, rgba(0,0,0,0) 0px, rgba(0,0,0,0) 1px, rgba(0,255,65,.008) 1px, rgba(0,255,65,.008) 2px);
}
.shell { width:min(1120px, calc(100% - 32px)); margin:0 auto; padding:28px 0; }
.top { display:flex; justify-content:space-between; gap:20px; align-items:start; margin-bottom:18px; }
.brand { color:var(--green-primary); font-weight:700; font-size:18px; }
.lede { color:var(--text-secondary); margin-top:8px; line-height:1.55; max-width:780px; }
.status { color:var(--cyan-accent); border:1px solid var(--border-default); padding:8px 10px; border-radius:var(--radius-md); background:var(--bg-secondary); }
.grid { display:grid; grid-template-columns: .92fr 1.08fr; gap:16px; align-items:stretch; }
.card { background:var(--bg-terminal); border:1px solid var(--border-green); border-radius:var(--radius-lg); overflow:hidden; }
.head { display:flex; align-items:center; gap:8px; background:var(--bg-terminal-header); padding:10px 12px; color:var(--text-heading); }
.dot { width:10px; height:10px; display:inline-block; border-radius:50%; }
.red { background:#ff5f56; } .yellow { background:#ffbd2e; } .green { background:#27c93f; }
.title { margin-left:6px; color:var(--text-heading); }
.body { padding:16px; }
label { display:block; color:var(--text-secondary); margin:0 0 7px; font-size:13px; }
input[type=password], input[type=text] {
  width:100%; background:var(--bg-secondary); color:var(--text-primary);
  border:1px solid var(--border-default); border-radius:var(--radius-md);
  padding:12px; font:inherit; outline:none;
}
input:focus { border-color:var(--green-primary); box-shadow:0 0 0 2px rgba(0,255,65,.12); }
.field { margin-bottom:14px; }
.row { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-top:12px; }
.check { display:flex; align-items:center; gap:9px; color:var(--text-secondary); font-size:13px; }
button {
  appearance:none; border:1px solid var(--border-default); border-radius:var(--radius-md);
  background:var(--bg-terminal); color:var(--green-primary); padding:11px 14px;
  font:inherit; cursor:pointer;
}
button.primary { background:var(--green-primary); color:#001806; border-color:var(--green-primary); font-weight:700; }
button:hover { color:var(--cyan-accent); border-color:var(--cyan-accent); }
button.primary:hover { background:var(--green-dim); color:#001806; }
button:disabled { opacity:.45; cursor:not-allowed; }
.warn { border-left:3px solid var(--yellow-accent); color:var(--text-secondary); padding-left:12px; line-height:1.55; font-size:13px; }
.log {
  height:430px; overflow:auto; white-space:pre-wrap; line-height:1.5; font-size:13px;
  background:#050912; color:var(--text-primary); border-top:1px solid var(--border-default);
  padding:16px;
}
.log .err { color:var(--red-accent); } .log .ok { color:var(--green-primary); } .log .warnline { color:var(--yellow-accent); }
@media (max-width: 860px) { .grid { grid-template-columns:1fr; } .top { flex-direction:column; } .log { height:340px; } }
</style>
</head>
<body>
<main class="shell">
  <section class="top">
    <div>
      <div class="brand">&gt; binance-tax-fr</div>
      <div class="lede">Calcul local des plus/minus-values crypto 2025 selon l'article 150 VH bis. Les cles API restent sur cette machine.</div>
    </div>
    <div class="status" id="status">idle</div>
  </section>
  <section class="grid">
    <div class="card">
      <div class="head"><span class="dot red"></span><span class="dot yellow"></span><span class="dot green"></span><span class="title">config.env</span></div>
      <div class="body">
        <div class="field"><label for="apiKey">BINANCE_API_KEY</label><input id="apiKey" type="text" autocomplete="off"></div>
        <div class="field"><label for="apiSecret">BINANCE_API_SECRET</label><input id="apiSecret" type="password" autocomplete="off"></div>
        <p class="warn">Utilise une cle Binance en lecture seule. Le rapport HTML, le cache et le fichier .env sont ecrits a cote de l'application.</p>
        <div class="row">
          <label class="check"><input id="saveEnv" type="checkbox" checked> sauvegarder dans .env local</label>
          <div>
            <button id="openReport">ouvrir le rapport</button>
            <button class="primary" id="run">&gt; lancer le calcul</button>
          </div>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="head"><span class="dot red"></span><span class="dot yellow"></span><span class="dot green"></span><span class="title">runtime.log</span></div>
      <div class="log" id="log"></div>
    </div>
  </section>
</main>
<script>
const $ = (id) => document.getElementById(id);
let last = "";
function paintLog(lines) {
  const html = lines.map((l) => {
    const esc = l.replace(/[&<>]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
    const cls = l.includes("[ERROR]") ? "err" : (l.includes("[OK]") ? "ok" : (l.includes("WARNING") ? "warnline" : ""));
    return `<span class="${cls}">${esc}</span>`;
  }).join("\\n");
  if (html !== last) { $("log").innerHTML = html; $("log").scrollTop = $("log").scrollHeight; last = html; }
}
async function refresh() {
  const s = await fetch("/api/status").then(r => r.json());
  $("status").textContent = s.status;
  $("run").disabled = s.running;
  $("openReport").disabled = !s.report || s.status === "running";
  paintLog(s.logs || []);
}
async function loadConfig() {
  const c = await fetch("/api/config").then(r => r.json());
  $("apiKey").value = c.apiKey || "";
  $("apiSecret").value = c.apiSecret || "";
}
$("run").onclick = async () => {
  const res = await fetch("/api/run", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({
    apiKey:$("apiKey").value, apiSecret:$("apiSecret").value, saveEnv:$("saveEnv").checked
  })});
  if (!res.ok) { const data = await res.json(); alert(data.error || "Erreur"); }
  refresh();
};
$("openReport").onclick = () => fetch("/api/open-report");
loadConfig(); refresh(); setInterval(refresh, 1000);
</script>
</body>
</html>"""


def serve(open_browser: bool = True, port: int = 0) -> None:
    logging.getLogger("binance-tax-fr").addHandler(QueueLogHandler())
    server = ThreadingHTTPServer((HOST, port), AppHandler)
    url = f"http://{HOST}:{server.server_port}/"
    append_log(f"$ open {url}")
    if open_browser:
        webbrowser.open(url)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        while not SHUTDOWN_EVENT.wait(0.5):
            pass
    finally:
        server.shutdown()
        server.server_close()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    serve(open_browser=not args.no_browser, port=args.port)


if __name__ == "__main__":
    main()
