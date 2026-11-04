"""echo.test: logs every incoming request as one JSON line on stdout."""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        print(json.dumps({"ts": time.time(), "method": self.command, "path": self.path,
                          "headers": dict(self.headers), "body": body.decode("utf-8", "replace")}),
              flush=True)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    do_GET = do_POST = do_PUT = do_OPTIONS = handle_any

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 80), H).serve_forever()
