"""The benchmark's web pages, on two origins so the payment form is a cross-origin frame.
Every page reports what was done in it to /record on its own origin, and the server appends
that to the log file, so a task is judged by what the page saw.

    python web.py LOG PORT_A PORT_B HEADLINE
"""

from __future__ import annotations

import html
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOG, PORT_A, PORT_B, HEADLINE = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
PRODUCTS = ("Brass desk lamp", "Paper floor lamp", "Clamp lamp", "Lamp shade, linen",
            "Bedside lamp")
RECORD = """<script>
function record(event) {
  return fetch('/record', {method: 'POST', body: JSON.stringify(event)});
}
</script>"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"{RECORD}<body style='font:16px sans-serif;margin:24px'>{body}</body>")


def shop() -> str:
    return page("Bench shop", """
<h1>Bench shop</h1>
<form action="/results" onsubmit="record({event: 'search', q: this.q.value})">
  <input name="q" type="search" placeholder="Search products" aria-label="Search products">
  <button>Search</button>
</form>
<p>Lamps, chairs and other things for a desk.</p>""")


def results(q: str) -> str:
    items = "".join(f"<li><a href='/product/{i + 1}'>{html.escape(p)}</a></li>"
                    for i, p in enumerate(PRODUCTS))
    return page("Bench shop results", f"<h1>Results for {html.escape(q)}</h1><ul>{items}</ul>")


def product(n: int) -> str:
    name = PRODUCTS[n - 1] if 1 <= n <= len(PRODUCTS) else "Unknown"
    return page(name, f"<h1>{html.escape(name)}</h1>"
                      f"<script>record({{event: 'product', n: {n}}})</script>")


def checkout() -> str:
    return page("Bench checkout", f"""
<h1>Checkout</h1><p>Order total: $42.00. Pay below.</p>
<iframe src="http://localhost:{PORT_B}/pay" title="Payment"
        style="width:460px;height:200px;border:1px solid #999"></iframe>""")


def pay() -> str:
    return page("Payment", """
<form onsubmit="event.preventDefault(); record({event: 'pay', name: this.holder.value})
                  .then(() => document.getElementById('done').textContent = 'Paid')">
  <input name="holder" placeholder="Cardholder name" aria-label="Cardholder name">
  <button>Pay</button>
</form><p id="done"></p>""")


def careers() -> str:
    return page("Bench careers", """
<h1>Apply</h1>
<form onsubmit="event.preventDefault(); const f = this.resume.files[0];
  (f ? f.arrayBuffer().then(b => crypto.subtle.digest('SHA-256', b)).then(d =>
     record({event: 'apply', name: f.name, size: f.size,
             sha256: Array.from(new Uint8Array(d)).map(x => x.toString(16).padStart(2, '0'))
                          .join('')}))
     : record({event: 'apply', name: null}))
  .then(() => document.getElementById('done').textContent = 'Application sent')">
  <label>Résumé <input name="resume" type="file"></label>
  <button>Submit application</button>
</form><p id="done"></p>""")


def news() -> str:
    return page("Bench news", f"<h1>{html.escape(HEADLINE)}</h1><p>More soon.</p>")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, body: str, status: int = 200) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        port = self.server.server_address[1]
        routes = {"/": shop, "/checkout": checkout, "/careers": careers, "/news": news}
        if port == PORT_B:
            routes = {"/pay": pay}
        if url.path in routes:
            return self._send(routes[url.path]())
        if port == PORT_A and url.path == "/results":
            return self._send(results(parse_qs(url.query).get("q", [""])[0]))
        if port == PORT_A and url.path.startswith("/product/"):
            return self._send(product(int(url.path.rsplit("/", 1)[1] or 0)))
        self._send("not found", 404)

    def do_POST(self):
        if self.path != "/record":
            return self._send("not found", 404)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            event = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            event = {"bad": True}
        event["at"] = time.time()
        with open(LOG, "a") as f:
            f.write(json.dumps(event) + "\n")
        self._send("ok")


for port in (PORT_A, PORT_B):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
threading.Event().wait()
