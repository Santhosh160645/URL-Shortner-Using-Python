#!/usr/bin/env python3
"""
Pure-Python URL shortener with a simple web UI and click counting.

Run:
    python shortener.py

Open:
    http://localhost:8000

Features:
- Web UI at "/" with form to create short URLs and a table showing stored URLs, clicks, and last access.
- POST /shorten (JSON or form) to create short URLs.
- GET /<code> redirects and increments click count + updates last_accessed.
- GET /api/urls returns JSON list.
- DELETE /api/urls/<code> returns JSON (also a POST /api/delete for the HTML form).
- Automatically creates or migrates the SQLite DB (shortener.db).
"""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote_plus, unquote_plus
import sqlite3
import json
import sys
import os
import time
import html

DB_PATH = "shortener.db"
HOST = "0.0.0.0"
PORT = 8000
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE = len(ALPHABET)


def encode_base62(n: int) -> str:
    if n == 0:
        return ALPHABET[0]
    s = []
    while n > 0:
        n, r = divmod(n, BASE)
        s.append(ALPHABET[r])
    return "".join(reversed(s))


def is_valid_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def init_db(path=DB_PATH):
    need_init = not os.path.exists(path)
    conn = sqlite3.connect(path)
    cur = conn.cursor()

    if need_init:
        cur.execute(
            """
            CREATE TABLE urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                long_url TEXT NOT NULL,
                short_code TEXT UNIQUE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                clicks INTEGER DEFAULT 0,
                last_accessed DATETIME
            )
            """
        )
        conn.commit()
        conn.close()
        return

    # Migration: ensure 'clicks' and 'last_accessed' columns exist (safe to run every start)
    cur.execute("PRAGMA table_info(urls)")
    cols = {row[1] for row in cur.fetchall()}
    if "clicks" not in cols:
        cur.execute("ALTER TABLE urls ADD COLUMN clicks INTEGER DEFAULT 0")
    if "last_accessed" not in cols:
        cur.execute("ALTER TABLE urls ADD COLUMN last_accessed DATETIME")
    conn.commit()
    conn.close()


class ShortenerHandler(BaseHTTPRequestHandler):
    server_version = "PurePyShortener/0.2"

    def _db_conn(self):
        # new connection per request/handler
        return sqlite3.connect(DB_PATH)

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, text, status=200):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/shorten":
            return self._handle_shorten_post(parsed)
        if parsed.path == "/api/delete" or parsed.path == "/api/delete/":
            return self._handle_delete_post(parsed)
        # fallback
        return self.send_error(404, "Not Found")

    def _handle_shorten_post(self, parsed):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")

        long_url = None
        try:
            if content_type.startswith("application/json"):
                data = json.loads(raw.decode("utf-8") or "{}")
                long_url = data.get("url") or data.get("long_url")
            else:
                qs = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
                long_url = qs.get("url", [None])[0] or qs.get("long_url", [None])[0]
        except Exception:
            return self.send_json({"error": "invalid request body"}, status=400)

        if not long_url or not is_valid_url(long_url):
            # If this was a form from the UI, redirect back with an error param
            referer = self.headers.get("Referer", "/")
            loc = "/?error=invalid+url"
            self.send_response(303)
            self.send_header("Location", loc)
            self.end_headers()
            return

        conn = self._db_conn()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO urls (long_url) VALUES (?)", (long_url,))
            rowid = cur.lastrowid
            code = encode_base62(rowid)
            cur.execute("UPDATE urls SET short_code = ? WHERE id = ?", (code, rowid))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            conn.close()
            return self.send_json({"error": "database error"}, status=500)
        finally:
            conn.close()

        host = self.headers.get("Host", f"{self.server.server_address[0]}:{self.server.server_address[1]}")
        short_url = f"http://{host.rstrip('/')}/{code}"

        # If request came from browser form, redirect back to index with created code
        if content_type.startswith("application/json"):
            return self.send_json({"short_url": short_url, "code": code, "id": rowid}, status=201)
        else:
            # redirect to index and show created code in query
            loc = f"/?created={quote_plus(code)}"
            self.send_response(303)
            self.send_header("Location", loc)
            self.end_headers()
            return

    def _handle_delete_post(self, parsed):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        qs = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        code = qs.get("code", [None])[0]
        if not code:
            self.send_response(303)
            self.send_header("Location", "/?error=missing+code")
            self.end_headers()
            return

        conn = self._db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id FROM urls WHERE short_code = ? LIMIT 1", (code,))
        row = cur.fetchone()
        if not row:
            conn.close()
            self.send_response(303)
            self.send_header("Location", "/?error=not+found")
            self.end_headers()
            return
        cur.execute("DELETE FROM urls WHERE short_code = ?", (code,))
        conn.commit()
        conn.close()
        self.send_response(303)
        self.send_header("Location", "/?deleted=" + quote_plus(code))
        self.end_headers()
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            return self._serve_index(qs)

        if path.startswith("/api/urls"):
            if path == "/api/urls":
                # list urls as JSON
                conn = self._db_conn()
                cur = conn.cursor()
                cur.execute("SELECT id, short_code, long_url, created_at, clicks, last_accessed FROM urls ORDER BY created_at DESC")
                rows = cur.fetchall()
                conn.close()
                out = []
                for r in rows:
                    out.append({
                        "id": r[0],
                        "code": r[1],
                        "url": r[2],
                        "created_at": r[3],
                        "clicks": r[4] or 0,
                        "last_accessed": r[5]
                    })
                return self.send_json(out)
            return self.send_error(404, "Not Found")

        # otherwise treat as short code redirect: GET /<code>
        code = path.lstrip("/")
        if not code:
            return self.send_error(404, "Not Found")

        conn = self._db_conn()
        cur = conn.cursor()
        cur.execute("SELECT long_url FROM urls WHERE short_code = ? LIMIT 1", (code,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return self.send_error(404, "Short code not found")
        long_url = row[0]
        # increment clicks and set last_accessed
        cur.execute(
            "UPDATE urls SET clicks = COALESCE(clicks,0) + 1, last_accessed = CURRENT_TIMESTAMP WHERE short_code = ?",
            (code,),
        )
        conn.commit()
        conn.close()
        # redirect
        self.send_response(302)
        self.send_header("Location", long_url)
        self.end_headers()
        return

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/urls/"):
            return self.send_error(404, "Not Found")
        code = path[len("/api/urls/") :]
        if not code:
            return self.send_error(400, "Missing code")

        conn = self._db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id FROM urls WHERE short_code = ? LIMIT 1", (code,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return self.send_json({"error": "not found"}, status=404)
        cur.execute("DELETE FROM urls WHERE short_code = ?", (code,))
        conn.commit()
        conn.close()
        return self.send_json({"deleted": code})

    def _serve_index(self, qs):
        # fetch URLs
        conn = self._db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, short_code, long_url, created_at, clicks, last_accessed FROM urls ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()

        created = qs.get("created", [None])[0]
        deleted = qs.get("deleted", [None])[0]
        error = qs.get("error", [None])[0]

        host = self.headers.get("Host", f"{self.server.server_address[0]}:{self.server.server_address[1]}")
        base = f"http://{host.rstrip('/')}"

        rows_html = ""
        for r in rows:
            id_, code, long_url, created_at, clicks, last_accessed = r
            esc_url = html.escape(long_url)
            short_link = f"{base}/{html.escape(code)}" if code else ""
            rows_html += f"""
            <tr>
                <td><a href="{short_link}" target="_blank" rel="noopener noreferrer">{html.escape(code or '')}</a></td>
                <td><a href="{esc_url}" target="_blank" rel="noopener noreferrer">{esc_url}</a></td>
                <td>{clicks or 0}</td>
                <td>{html.escape(created_at or '')}</td>
                <td>{html.escape(last_accessed or '')}</td>
                <td>
                    <form method="post" action="/api/delete" onsubmit="return confirm('Delete this short URL?');">
                        <input type="hidden" name="code" value="{html.escape(code or '')}">
                        <button type="submit">Delete</button>
                    </form>
                </td>
            </tr>
            """

        # small page
        page = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Pure Python URL Shortener</title>
<style>
body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial; padding: 24px; max-width: 1000px; }}
input[type="text"] {{ width: 60%; padding: 8px; }}
button {{ padding: 6px 10px; }}
table {{ border-collapse: collapse; margin-top: 16px; width:100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background: #f7f7f7; }}
.notice {{ padding: 8px; margin: 8px 0; border-radius: 4px; }}
.success {{ background: #e6ffed; border: 1px solid #b9f2c7; }}
.error {{ background: #ffecec; border: 1px solid #f2b9b9; }}
</style>
</head>
<body>
<h1>Pure Python URL Shortener</h1>

{('<div class="notice success">Created short code: <strong>' + html.escape(created) + '</strong> — <a href="' + base + '/' + html.escape(created) + '" target="_blank">Open</a></div>') if created else ''}
{('<div class="notice success">Deleted: <strong>' + html.escape(deleted) + '</strong></div>') if deleted else ''}
{('<div class="notice error">Error: ' + html.escape(unquote_plus(error)) + '</div>') if error else ''}

<form id="shorten-form" method="post" action="/shorten">
    <input type="text" name="url" placeholder="https://example.com/very/long/url" required>
    <button type="submit">Shorten</button>
</form>

<p>Or use the API: POST /shorten (JSON or form). Redirects: GET /&lt;code&gt;</p>

<table>
    <thead>
        <tr>
            <th>Code</th><th>Original URL</th><th>Clicks</th><th>Created At</th><th>Last Accessed</th><th>Actions</th>
        </tr>
    </thead>
    <tbody>
        {rows_html}
    </tbody>
</table>

<script>
document.getElementById('shorten-form').addEventListener('submit', function(e) {{
    // nothing extra needed; simple form submit is fine
}});
</script>
</body>
</html>
"""
        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # Less-verbose logging
    def log_message(self, format, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), time.strftime("%d/%b/%Y %H:%M:%S"), format % args))


def run(host=HOST, port=PORT):
    init_db()
    addr = (host, port)
    with ThreadingHTTPServer(addr, ShortenerHandler) as httpd:
        print(f"Serving on http://{host}:{port} (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down")


if __name__ == "__main__":
    run()