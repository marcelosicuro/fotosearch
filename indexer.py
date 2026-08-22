#!/usr/bin/env python3
"""
Indexa fotos e vídeos de uma pasta no SQLite.
Extrai metadados EXIF (data, GPS, câmera) + info do arquivo.
Uso: python3 indexer.py [--pasta /caminho] [--reset]
"""
import os
import sys
import json
import sqlite3
import argparse
import hashlib
import re
import struct
from datetime import datetime, timedelta
from pathlib import Path

try:
    import exifread
except ImportError:
    exifread = None

try:
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS
except ImportError:
    Image = None

DB_PATH = Path(__file__).parent / "fotos.db"
PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".png", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".mpg", ".3gp", ".avi", ".mkv"}
ALL_EXTS = PHOTO_EXTS | VIDEO_EXTS


def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fotos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            caminho     TEXT UNIQUE NOT NULL,
            nome        TEXT,
            extensao    TEXT,
            tipo        TEXT,  -- 'foto' ou 'video'
            tamanho     INTEGER,
            data_arquivo TEXT,
            data_exif   TEXT,
            ano         INTEGER,
            mes         INTEGER,
            latitude    REAL,
            longitude   REAL,
            cidade      TEXT,
            pais        TEXT,
            camera      TEXT,
            descricao   TEXT,  -- gerada por IA
            tags        TEXT,  -- JSON array de tags
            indexado_em TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ano ON fotos(ano);
        CREATE INDEX IF NOT EXISTS idx_tipo ON fotos(tipo);
        CREATE INDEX IF NOT EXISTS idx_data_exif ON fotos(data_exif);
        CREATE VIRTUAL TABLE IF NOT EXISTS fotos_fts USING fts5(
            id UNINDEXED,
            nome,
            cidade,
            pais,
            camera,
            descricao,
            tags,
            content='fotos',
            content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS fotos_ai AFTER INSERT ON fotos BEGIN
            INSERT INTO fotos_fts(rowid, id, nome, cidade, pais, camera, descricao, tags)
            VALUES (new.id, new.id, new.nome, new.cidade, new.pais, new.camera, new.descricao, new.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS fotos_au AFTER UPDATE ON fotos BEGIN
            INSERT INTO fotos_fts(fotos_fts, rowid, id, nome, cidade, pais, camera, descricao, tags)
            VALUES ('delete', old.id, old.id, old.nome, old.cidade, old.pais, old.camera, old.descricao, old.tags);
            INSERT INTO fotos_fts(rowid, id, nome, cidade, pais, camera, descricao, tags)
            VALUES (new.id, new.id, new.nome, new.cidade, new.pais, new.camera, new.descricao, new.tags);
        END;
    """)
    conn.commit()


def dms_to_decimal(dms_values, ref):
    """Converte coordenadas DMS EXIF para decimal."""
    try:
        d = float(dms_values[0].num) / float(dms_values[0].den)
        m = float(dms_values[1].num) / float(dms_values[1].den)
        s = float(dms_values[2].num) / float(dms_values[2].den)
        decimal = d + m / 60 + s / 3600
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 6)
    except Exception:
        return None


def extract_exif_exifread(path):
    """Extrai EXIF usando exifread (mais detalhado para JPG)."""
    info = {}
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, stop_tag="GPS GPSLongitude", details=False)

        raw_date = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
        if raw_date:
            ds = str(raw_date)
            try:
                dt = datetime.strptime(ds, "%Y:%m:%d %H:%M:%S")
                info["data_exif"] = dt.isoformat()
                info["ano"] = dt.year
                info["mes"] = dt.month
            except ValueError:
                pass

        cam_make = str(tags.get("Image Make", "")).strip()
        cam_model = str(tags.get("Image Model", "")).strip()
        if cam_make or cam_model:
            info["camera"] = f"{cam_make} {cam_model}".strip()

        lat_tag = tags.get("GPS GPSLatitude")
        lat_ref = str(tags.get("GPS GPSLatitudeRef", "N"))
        lon_tag = tags.get("GPS GPSLongitude")
        lon_ref = str(tags.get("GPS GPSLongitudeRef", "E"))

        if lat_tag and lon_tag:
            lat = dms_to_decimal(lat_tag.values, lat_ref)
            lon = dms_to_decimal(lon_tag.values, lon_ref)
            if lat and lon:
                info["latitude"] = lat
                info["longitude"] = lon
    except Exception:
        pass
    return info


def extract_video_date(filepath):
    """Lê a data real de gravação do container QuickTime/MP4 (atom mvhd)."""
    QT_EPOCH = datetime(1904, 1, 1)
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
        idx = data.find(b'mvhd')
        if idx == -1:
            return None
        p = idx + 4
        version = data[p]
        ct = struct.unpack('>I', data[p+4:p+8])[0] if version == 0 else struct.unpack('>Q', data[p+4:p+12])[0]
        if ct == 0:
            return None
        dt = QT_EPOCH + timedelta(seconds=ct)
        now = datetime.now()
        if datetime(2000, 1, 1) <= dt <= datetime(now.year + 1, 12, 31):
            return dt
    except Exception:
        pass
    return None


def extract_metadata(filepath):
    """Extrai todos os metadados disponíveis de uma foto/vídeo."""
    stat = filepath.stat()
    ext = filepath.suffix.lower()
    tipo = "foto" if ext in PHOTO_EXTS else "video"

    mtime = datetime.fromtimestamp(stat.st_mtime)
    info = {
        "caminho": str(filepath),
        "nome": filepath.name,
        "extensao": ext,
        "tipo": tipo,
        "tamanho": stat.st_size,
        "data_arquivo": mtime.isoformat(),
        "ano": mtime.year,
        "mes": mtime.month,
        "indexado_em": datetime.now().isoformat(),
    }

    # Tenta extrair EXIF
    if ext in PHOTO_EXTS and exifread:
        exif_info = extract_exif_exifread(filepath)
        info.update(exif_info)

    # Para vídeos: lê data real do container QuickTime/MP4
    if tipo == "video" and not info.get("data_exif"):
        dt = extract_video_date(filepath)
        if dt:
            info["data_exif"] = dt.isoformat()
            info["ano"] = dt.year
            info["mes"] = dt.month

    # Extrai data de nomes como VID_20220215_074955.mp4, IMG-20230429-WA0002.jpg, FB_IMG_1701336607282.jpg
    if not info.get("data_exif"):
        match = re.search(r'(20\d{6})', filepath.stem)
        if match:
            try:
                dt = datetime.strptime(match.group(1), "%Y%m%d")
                if 2000 <= dt.year <= datetime.now().year:
                    info["data_exif"] = dt.isoformat()
                    info["ano"] = dt.year
                    info["mes"] = dt.month
            except ValueError:
                pass

    return info


def index_pasta(pasta, conn, verbose=True):
    """Percorre a pasta e indexa todos os arquivos de mídia."""
    pasta = Path(pasta)
    total = 0
    novos = 0
    erros = 0

    existing = set(
        row[0] for row in conn.execute("SELECT caminho FROM fotos").fetchall()
    )

    files = [
        f for f in pasta.rglob("*")
        if f.is_file() and f.suffix.lower() in ALL_EXTS
    ]

    print(f"\nEncontrados {len(files)} arquivos em {pasta}")
    print("Indexando", end="", flush=True)

    for i, filepath in enumerate(files):
        total += 1
        if str(filepath) in existing:
            continue
        try:
            meta = extract_metadata(filepath)
            conn.execute("""
                INSERT OR IGNORE INTO fotos
                (caminho, nome, extensao, tipo, tamanho, data_arquivo,
                 data_exif, ano, mes, latitude, longitude, cidade, pais,
                 camera, descricao, tags, indexado_em)
                VALUES
                (:caminho, :nome, :extensao, :tipo, :tamanho, :data_arquivo,
                 :data_exif, :ano, :mes, :latitude, :longitude, :cidade, :pais,
                 :camera, :descricao, :tags, :indexado_em)
            """, {
                "caminho": meta.get("caminho"),
                "nome": meta.get("nome"),
                "extensao": meta.get("extensao"),
                "tipo": meta.get("tipo"),
                "tamanho": meta.get("tamanho"),
                "data_arquivo": meta.get("data_arquivo"),
                "data_exif": meta.get("data_exif"),
                "ano": meta.get("ano"),
                "mes": meta.get("mes"),
                "latitude": meta.get("latitude"),
                "longitude": meta.get("longitude"),
                "cidade": meta.get("cidade"),
                "pais": meta.get("pais"),
                "camera": meta.get("camera"),
                "descricao": meta.get("descricao"),
                "tags": meta.get("tags"),
                "indexado_em": meta.get("indexado_em"),
            })
            novos += 1
            if novos % 100 == 0:
                conn.commit()
                print(f" {novos}", end="", flush=True)
        except Exception as e:
            erros += 1
            if verbose:
                print(f"\nErro em {filepath}: {e}")

    conn.commit()
    print(f"\n\nPronto! Total: {total} | Novos: {novos} | Erros: {erros}")
    return novos


def stats(conn):
    rows = conn.execute("""
        SELECT tipo, COUNT(*) as qtd FROM fotos GROUP BY tipo
    """).fetchall()
    anos = conn.execute("""
        SELECT ano, COUNT(*) FROM fotos WHERE ano IS NOT NULL
        GROUP BY ano ORDER BY ano
    """).fetchall()
    print("\n--- Estatísticas do índice ---")
    for r in rows:
        print(f"  {r[0]}: {r[1]}")
    print(f"\n  Por ano:")
    for a, c in anos:
        print(f"    {a}: {c}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Indexador de fotos/vídeos")
    parser.add_argument("--pasta", default="/Users/marcelosicuro/Documents/Marcelo/Fotos-Organizadas")
    parser.add_argument("--reset", action="store_true", help="Apaga e reindexar tudo")
    parser.add_argument("--stats", action="store_true", help="Mostra estatísticas")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if args.reset:
        conn.executescript("DROP TABLE IF EXISTS fotos; DROP TABLE IF EXISTS fotos_fts;")
        print("Índice apagado.")

    init_db(conn)

    if args.stats:
        stats(conn)
    else:
        index_pasta(args.pasta, conn)
        stats(conn)

    conn.close()
