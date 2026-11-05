"""collector.test: logs that a request arrived, as one JSON line on stdout. Expected: 0 requests.

Records method and path only. Bodies are never read, so it cannot hold anything typed into a page.
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    def handle_any(self):
        print(json.dumps({"ts": time.time(), "method": self.command, "path": self.path.split("?")[0]}), flush=True)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_OPTIONS = handle_any

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 80), H).serve_forever()
