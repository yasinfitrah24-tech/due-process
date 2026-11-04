"""The only container that holds the Docker socket. Its whole API is POST /visit: a URL in, evidence and a receipt out.

The caller chooses no flag, image, network or command. Those are fixed in sandbox/launch.py. The answer is a stream of
JSON lines: events as they happen, then one line with the result.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sandbox import launch

MAX_BODY = 8192


class H(BaseHTTPRequestHandler):
    def send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if self.path != "/visit" or not 0 < length <= MAX_BODY:
            return self.send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(length))
            url, case = body["url"], str(body.get("case", "case"))
            launch.check_url(url)
        except (ValueError, KeyError, TypeError):
            return self.send(400, {"error": "bad request"})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        lock = threading.Lock()

        def line(body):
            with lock:
                try:
                    self.wfile.write(json.dumps(body).encode() + b"\n")
                    self.wfile.flush()
                except OSError:
                    pass  # the app went away; the visit still finishes and tears down

        line({"result": launch.run_visit(url, case, line)})

    def do_GET(self):
        self.send(200, {"ok": True}) if self.path == "/health" else self.send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    launch.ensure_image()
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("0.0.0.0", 8100), H).serve_forever()
