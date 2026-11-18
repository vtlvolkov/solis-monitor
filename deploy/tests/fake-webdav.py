"""A stand-in WebDAV server for deploy/tests/test-install.sh: the part of WebDAV `solis-monitor backup` uses (MKCOL,
PUT, PROPFIND, MOVE, DELETE) plus Nextcloud's chunked upload (MKCOL under /remote.php/dav/uploads/<user>/<id>,
numbered pieces, MOVE of <id>/.file joins them). Files live under ROOT, at their URL path.

    python3 -I fake-webdav.py PORT ROOT USER PASSWORD [MAX_BODY]

MAX_BODY (bytes): a bigger request body is cut off unanswered, as the Umbrel Nextcloud did with one 43 MB PUT.
Each request is logged as "METHOD PATH STATUS" to ROOT/../webdav.log.
"""
import base64
import os
import shutil
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

PORT, ROOT, USER, PASSWORD = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
MAX_BODY = int(sys.argv[5]) if len(sys.argv) > 5 else 0
LOG = os.path.join(os.path.dirname(ROOT.rstrip("/")), "webdav.log")


def fs(url_path: str) -> str:
    p = os.path.normpath("/" + unquote(urlsplit(url_path).path).strip("/"))
    return os.path.join(ROOT, p.lstrip("/"))


class Dav(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def answer(self, code: int, body: bytes = b"", ctype: str = "text/plain"):
        with open(LOG, "a") as f:
            f.write(f"{self.command} {unquote(self.path)} {code}\n")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self) -> bytes | None:
        n = int(self.headers.get("Content-Length") or 0)
        if MAX_BODY and n > MAX_BODY:
            with open(LOG, "a") as f:
                f.write(f"{self.command} {unquote(self.path)} cut\n")
            self.close_connection = True
            self.connection.shutdown(2)
            return None
        return self.rfile.read(n) if n else b""

    def authorised(self) -> bool:
        want = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        if self.headers.get("Authorization") == want:
            return True
        self.body()
        self.answer(401)
        return False

    def do_MKCOL(self):
        if not self.authorised():
            return
        p = fs(self.path)
        if os.path.exists(p):
            return self.answer(405)
        if not os.path.isdir(os.path.dirname(p)):
            return self.answer(409)
        os.mkdir(p)
        self.answer(201)

    def do_PUT(self):
        if not self.authorised():
            return
        data = self.body()
        if data is None:
            return
        p = fs(self.path)
        if not os.path.isdir(os.path.dirname(p)):
            return self.answer(409)
        existed = os.path.exists(p)
        with open(p, "wb") as f:
            f.write(data)
        self.answer(204 if existed else 201)

    def do_DELETE(self):
        if not self.authorised():
            return
        p = fs(self.path)
        if not os.path.exists(p):
            return self.answer(404)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        self.answer(204)

    def do_MOVE(self):
        if not self.authorised():
            return
        src, dest = fs(self.path), fs(self.headers.get("Destination", ""))
        if self.path.endswith("/.file"):                        # Nextcloud: join the pieces, in order
            folder = os.path.dirname(src)
            if not os.path.isdir(folder) or not os.path.isdir(os.path.dirname(dest)):
                return self.answer(409)
            with open(dest, "wb") as out:
                for piece in sorted(os.listdir(folder)):
                    with open(os.path.join(folder, piece), "rb") as f:
                        out.write(f.read())
            shutil.rmtree(folder)
            return self.answer(201)
        if not os.path.exists(src):
            return self.answer(404)
        os.replace(src, dest)
        self.answer(201)

    def do_PROPFIND(self):
        if not self.authorised():
            return
        self.body()
        p = fs(self.path)
        if not os.path.exists(p):
            return self.answer(404)
        base = urlsplit(self.path).path.rstrip("/")
        entries = [(base + ("/" if os.path.isdir(p) else ""), p)]
        if os.path.isdir(p) and self.headers.get("Depth") == "1":
            entries += [(f"{base}/{n}", os.path.join(p, n)) for n in sorted(os.listdir(p))]
        rows = "".join(
            f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>"
            + ("<d:resourcetype><d:collection/></d:resourcetype>" if os.path.isdir(f)
               else f"<d:getcontentlength>{os.path.getsize(f)}</d:getcontentlength>")
            + "</d:prop></d:propstat></d:response>" for href, f in entries)
        self.answer(207, f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">{rows}</d:multistatus>'.encode(), "application/xml")


ThreadingHTTPServer(("127.0.0.1", PORT), Dav).serve_forever()
