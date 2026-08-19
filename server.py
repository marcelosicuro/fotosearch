#!/usr/bin/env python3
"""
Servidor web de busca de fotos.
Uso: python3 server.py
Acesse: http://localhost:5050
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
import mimetypes

HEIC_CACHE_DIR = Path("/tmp/fotosearch_thumbs")
HEIC_CACHE_DIR.mkdir(exist_ok=True)


def heic_to_jpeg(heic_path):
    """Converte HEIC para JPEG usando sips (macOS). Retorna caminho do JPEG em cache."""
    key = hashlib.md5(str(heic_path).encode()).hexdigest()
    cache_path = HEIC_CACHE_DIR / f"{key}.jpg"
    if not cache_path.exists():
        subprocess.run(
            ["sips", "-s", "format", "jpeg", "-Z", "1200", str(heic_path), "--out", str(cache_path)],
            capture_output=True
        )
    return cache_path if cache_path.exists() else None

DB_PATH = Path(__file__).parent / "fotos.db"
STATIC_DIR = Path(__file__).parent
PORT = 5050

MONTH_NAMES = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def search_fotos(q="", ano=None, tipo=None, tem_gps=False, tem_ia=False, limit=60, offset=0):
    conn = get_db()
    params = []
    conditions = []

    if q and q.strip():
        # Full-text search
        q_clean = q.strip().replace('"', '""')
        conditions.append("""
            id IN (
                SELECT rowid FROM fotos_fts
                WHERE fotos_fts MATCH ?
            )
        """)
        params.append(f'"{q_clean}"*')

    if ano:
        conditions.append("ano = ?")
        params.append(int(ano))

    if tipo and tipo != "todos":
        conditions.append("tipo = ?")
        params.append(tipo)

    if tem_gps:
        conditions.append("latitude IS NOT NULL")

    if tem_ia:
        conditions.append("descricao IS NOT NULL")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    count_sql = f"SELECT COUNT(*) FROM fotos {where}"
    total = conn.execute(count_sql, params).fetchone()[0]

    sql = f"""
        SELECT id, caminho, nome, extensao, tipo, tamanho,
               data_exif, data_arquivo, ano, mes,
               latitude, longitude, cidade, pais, camera,
               descricao, tags
        FROM fotos {where}
        ORDER BY COALESCE(data_exif, data_arquivo) DESC
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(sql, params + [limit, offset]).fetchall()
    conn.close()

    results = []
    for r in rows:
        item = dict(r)
        # Formata data legível
        for dcol in ("data_exif", "data_arquivo"):
            if item.get(dcol):
                try:
                    dt = datetime.fromisoformat(item[dcol])
                    item[dcol + "_fmt"] = dt.strftime("%d/%m/%Y %H:%M")
                except Exception:
                    pass
        # Tamanho legível
        sz = item.get("tamanho", 0) or 0
        if sz > 1_000_000:
            item["tamanho_fmt"] = f"{sz/1_000_000:.1f} MB"
        elif sz > 1000:
            item["tamanho_fmt"] = f"{sz/1000:.0f} KB"
        else:
            item["tamanho_fmt"] = f"{sz} B"
        results.append(item)

    return {"total": total, "limit": limit, "offset": offset, "results": results}


def get_anos():
    conn = get_db()
    rows = conn.execute(
        "SELECT ano, COUNT(*) as c FROM fotos WHERE ano IS NOT NULL GROUP BY ano ORDER BY ano DESC"
    ).fetchall()
    conn.close()
    return [{"ano": r["ano"], "total": r["c"]} for r in rows]


def get_stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM fotos").fetchone()[0]
    fotos = conn.execute("SELECT COUNT(*) FROM fotos WHERE tipo='foto'").fetchone()[0]
    videos = conn.execute("SELECT COUNT(*) FROM fotos WHERE tipo='video'").fetchone()[0]
    com_gps = conn.execute("SELECT COUNT(*) FROM fotos WHERE latitude IS NOT NULL").fetchone()[0]
    com_descricao = conn.execute("SELECT COUNT(*) FROM fotos WHERE descricao IS NOT NULL").fetchone()[0]
    conn.close()
    return {
        "total": total, "fotos": fotos, "videos": videos,
        "com_gps": com_gps, "com_descricao": com_descricao
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
            mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", len(data))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def serve_media(self, path):
        """Serve arquivo com suporte a Range requests (necessário para vídeo)."""
        try:
            file_size = os.path.getsize(path)
            mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            range_header = self.headers.get("Range")

            if range_header:
                # Parseia "bytes=start-end"
                byte_range = range_header.replace("bytes=", "").split("-")
                start = int(byte_range[0]) if byte_range[0] else 0
                end = int(byte_range[1]) if byte_range[1] else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", length)
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with open(path, "rb") as f:
                    f.seek(start)
                    self.wfile.write(f.read(length))
            else:
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", file_size)
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with open(path, "rb") as f:
                    self.wfile.write(f.read())
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/delete":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                path = body.get("path", "")
                if not path or not os.path.exists(path):
                    self.send_json({"ok": False, "error": "não encontrado"}, 404)
                    return
                os.remove(path)
                conn = get_db()
                conn.execute("DELETE FROM fotos WHERE caminho = ?", (path,))
                conn.commit()
                conn.close()
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        def qp(key, default=None):
            return qs.get(key, [default])[0]

        if path == "/" or path == "/index.html":
            self.serve_file(STATIC_DIR / "index.html")
            return

        if path == "/api/search":
            result = search_fotos(
                q=qp("q", ""),
                ano=qp("ano"),
                tipo=qp("tipo", "todos"),
                tem_gps=qp("gps") == "1",
                tem_ia=qp("ia") == "1",
                limit=int(qp("limit", "60")),
                offset=int(qp("offset", "0")),
            )
            self.send_json(result)
            return

        if path == "/api/anos":
            self.send_json(get_anos())
            return

        if path == "/api/stats":
            self.send_json(get_stats())
            return

        if path == "/api/thumb":
            caminho = qp("path")
            if caminho and os.path.exists(caminho):
                ext = Path(caminho).suffix.lower()
                if ext == ".heic":
                    jpeg = heic_to_jpeg(caminho)
                    if jpeg:
                        self.serve_media(str(jpeg))
                    else:
                        self.send_response(404)
                        self.end_headers()
                    return
                if ext in {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".mpg", ".3gp"}:
                    self.serve_media(caminho)
                    return
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"Banco de dados não encontrado: {DB_PATH}")
        print("Execute primeiro: python3 indexer.py")
        exit(1)

    print(f"\nServidor rodando em http://localhost:{PORT}")
    print("Pressione Ctrl+C para parar.\n")
    server = ThreadingHTTPServer(("localhost", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
