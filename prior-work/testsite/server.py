"""TEST PAGE server for the detonation probe. Only reachable inside dp-net."""
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BANNER = '<p style="background:#fd0;padding:8px;font:bold 16px sans-serif">TEST PAGE &mdash; probe only</p>'


def page(title, body, head=""):
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>TEST PAGE - {title}</title>{head}</head>
<body>{BANNER}<h1>TEST PAGE: {title}</h1>{body}</body></html>""".encode()


STEP2 = page("step2", "<p>Client-side redirect to /form in 1.5s.</p>",
             head="<script>addEventListener('load',()=>setTimeout(()=>location.replace('/form'),1500))</script>")

FORM = page("form", """
<form id="login">
  <label>User <input type="text" name="username"></label><br>
  <label>Pass <input type="password" name="password"></label><br>
  <button type="button" id="go">Sign in</button>
</form>
<p id="hid" style="display:none">TEST PAGE hidden paragraph: secret-marker-42</p>
<a id="dl" href="/sample.txt" download>sample</a>
<script>
document.getElementById('go').addEventListener('click', () => {
  const f = new FormData(document.getElementById('login'));
  fetch('http://echo.test/submit', {method: 'POST', body: new URLSearchParams(f)});
});
addEventListener('load', () => document.getElementById('dl').click());
</script>""")

PLAIN = page("plain", """
<form action="/plain-submit" method="post">
  <label>Name <input type="text" name="name"></label><br>
  <label>Email <input type="text" name="email"></label><br>
  <button type="submit">Send</button>
</form>""")

SAMPLE = b"TEST PAGE sample download for the detonation probe.\n"


class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/start":
            self.send(302, b"", extra={"Location": "/step2"})
        elif p == "/step2":
            self.send(200, STEP2)
        elif p == "/form":
            self.send(200, FORM)
        elif p == "/plain":
            self.send(200, PLAIN)
        elif p == "/sample.txt":
            self.send(200, SAMPLE, "text/plain",
                      {"Content-Disposition": 'attachment; filename="sample.txt"'})
        elif p == "/hang":
            # Headers + partial body, then never finish.
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<!doctype html><title>TEST PAGE hang</title><p>TEST PAGE: hanging")
            self.wfile.flush()
            while True:
                time.sleep(3600)
        else:
            self.send(404, page("404", "<p>not found</p>"))

    def do_POST(self):
        self.send(200, page("posted", "<p>received</p>"))


if __name__ == "__main__":
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 80), H).serve_forever()
