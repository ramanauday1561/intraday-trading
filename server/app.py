"""Render web service for the Intraday Quant Desk.

What it does
  * Serves the app by mirroring the GitHub Pages site, so the Render URL always
    shows the same code and the same live data that the Actions pipeline deploys.
  * GET /egress-ip reports the public IP this service uses for OUTGOING requests
    (the address a broker such as Kite sees), and logs it at startup.
  * GET /healthz for Render's health check.

What it does NOT do
  * It holds no broker credentials and places, changes or cancels no orders.

Standard library only, so the build needs no dependencies.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("UPSTREAM", "https://ramanauday1561.github.io/intraday-trading").rstrip("/")
IP_SERVICES = ("https://api.ipify.org?format=json", "https://checkip.amazonaws.com")
UA = "intraday-quant-desk/1.0"
PASS_HEADERS = ("content-type", "cache-control", "last-modified", "etag")

_ip_cache = {"ip": None, "at": 0.0, "seen": []}
_ip_lock = threading.Lock()


def egress_ip(max_age=60):
    """Public IP of this instance's outbound traffic (cached for max_age seconds)."""
    with _ip_lock:
        if _ip_cache["ip"] and time.time() - _ip_cache["at"] < max_age:
            return _ip_cache["ip"]
    ip = None
    for url in IP_SERVICES:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=8) as r:
                body = r.read().decode().strip()
            ip = json.loads(body)["ip"] if body.startswith("{") else body
            break
        except Exception as e:  # try the next service
            print(f"egress-ip lookup via {url} failed: {e}", file=sys.stderr, flush=True)
    if ip:
        with _ip_lock:
            _ip_cache.update(ip=ip, at=time.time())
            if ip not in _ip_cache["seen"]:
                _ip_cache["seen"].append(ip)
    return ip


class Handler(BaseHTTPRequestHandler):
    server_version = "IntradayDesk/1.0"

    def _send(self, code, body, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("#", 1)[0]
        if path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        if path.startswith("/egress-ip"):
            ip = egress_ip()
            body = json.dumps({
                "egress_ip": ip,
                "seen_since_start": _ip_cache["seen"],
                "region": os.environ.get("RENDER_REGION") or os.environ.get("REGION"),
                "instance": os.environ.get("RENDER_INSTANCE_ID"),
                "note": "Outbound IP observed by external services. On Render this comes from the "
                        "region's shared outbound pool and is not guaranteed to be one fixed address.",
            }, indent=2).encode()
            return self._send(200 if ip else 502, body, extra={"Cache-Control": "no-store"})
        self._proxy(path)

    def _proxy(self, path):
        url = UPSTREAM + (path if path.startswith("/") else "/" + path)
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body, code, headers = r.read(), r.status, r.headers
        except urllib.error.HTTPError as e:
            body, code, headers = e.read(), e.code, e.headers
        except Exception as e:
            return self._send(502, json.dumps({"error": f"upstream unavailable: {e}"}).encode())
        extra = {k: headers[k] for k in PASS_HEADERS if headers.get(k) and k != "content-type"}
        self._send(code, body, headers.get("content-type", "application/octet-stream"), extra)

    def log_message(self, fmt, *args):
        print("%s %s" % (self.address_string(), fmt % args), flush=True)


def main():
    port = int(os.environ.get("PORT", "10000"))
    threading.Thread(target=lambda: print(f"EGRESS_IP={egress_ip()}", flush=True), daemon=True).start()
    print(f"Serving {UPSTREAM} on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
