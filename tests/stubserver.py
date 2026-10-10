"""Local HTTP stub that records every request that actually reaches it."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Stub:
    def __init__(self):
        self.hits = []          # (path_with_query, headers, monotonic_time)
        self._routes = {}       # path -> list of (status, headers, body); last entry repeats
        stub = self

        class H(BaseHTTPRequestHandler):
            def _respond(self):
                stub.hits.append((self.path, dict(self.headers), time.monotonic()))
                seq = stub._routes.get(self.path)
                if seq is None:
                    status, headers, body = 404, {}, b"not found"
                else:
                    status, headers, body = seq.pop(0) if len(seq) > 1 else seq[0]
                self.send_response(status)
                if "Content-Type" not in headers:
                    self.send_header("Content-Type", "text/plain; charset=utf-8" if self.path == "/robots.txt" else "text/html; charset=utf-8")
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._respond()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)          # drain the request body (T18: Workday-style JSON POSTs)
                self._respond()

            def log_message(self, *a):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self._srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()

    def route(self, path, *seq, status=200, body=b"ok", headers=None):
        """route(path, status=..., body=...) or route(path, (status, headers, body), ...)"""
        self._routes[path] = list(seq) if seq else [(status, headers or {}, body)]
        return self

    def robots(self, text=None, status=200):
        return self.route("/robots.txt", status=status, body=(text or "").encode())

    def paths(self):
        return [h[0] for h in self.hits]

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s
