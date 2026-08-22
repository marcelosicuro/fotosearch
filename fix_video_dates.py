#!/usr/bin/env python3
"""
Corrige data_exif dos vídeos que estão usando data_arquivo (data de organização).
Lê a data real de gravação do container QuickTime/MP4.
Uso: python3 fix_video_dates.py
"""
import sqlite3, struct
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "fotos.db"
QT_EPOCH = datetime(1904, 1, 1)


def extract_video_date(filepath):
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


conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, caminho, nome FROM fotos WHERE tipo='video' AND data_exif IS NULL"
).fetchall()

print(f"{len(rows)} vídeos sem data_exif. Processando...\n")

ok = sem_mvhd = erros = 0
for i, row in enumerate(rows, 1):
    if not Path(row['caminho']).exists():
        erros += 1
        continue
    dt = extract_video_date(row['caminho'])
    if dt:
        conn.execute(
            "UPDATE fotos SET data_exif=?, ano=?, mes=? WHERE id=?",
            (dt.isoformat(), dt.year, dt.month, row['id'])
        )
        ok += 1
        if i % 50 == 0:
            conn.commit()
            print(f"  {i}/{len(rows)} — último: {row['nome']} → {dt.date()}")
    else:
        sem_mvhd += 1

conn.commit()
conn.close()

print(f"\nConcluído: {ok} corrigidos | {sem_mvhd} sem mvhd | {erros} não encontrados")
