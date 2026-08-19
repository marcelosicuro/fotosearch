#!/usr/bin/env python3
"""
Enriquece o banco de fotos com descrições e tags via Gemini.
Uso: python3 enricher.py [--limit 1400] [--key SUA_CHAVE]

A chave também pode ser definida como variável de ambiente:
    export GEMINI_API_KEY="sua-chave"

Obtenha uma chave gratuita em: https://aistudio.google.com/apikey
"""
import argparse
import base64
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    print("Dependência não encontrada. Instale com:")
    print("  pip install google-genai")
    sys.exit(1)

DB_PATH = Path(__file__).parent / "fotos.db"
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".heic"}

PROMPT = (
    "Analise esta imagem e responda APENAS com um objeto JSON (sem markdown), "
    "com exatamente dois campos:\n"
    '- "descricao": descrição em português de 2 a 3 frases sobre o que aparece '
    "(pessoas, lugares, animais, objetos, ocasião)\n"
    '- "tags": lista de 5 a 10 palavras-chave em português em letras minúsculas '
    '(ex: ["praia","família","criança","férias","verão"])'
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def encode_image(path):
    ext = Path(path).suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",  ".gif": "image/gif",
        ".heic": "image/heic",
    }
    mime = mime_map.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    return mime, data


def extract_video_frame(path):
    """Extrai o frame do meio do vídeo usando ffmpeg."""
    try:
        # Descobre a duração
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(path)],
            capture_output=True, timeout=10
        )
        duration = 0
        if probe.returncode == 0:
            info = json.loads(probe.stdout)
            duration = float(info.get("format", {}).get("duration", 0))

        seek = max(0, duration / 2)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name

        result = subprocess.run(
            ["ffmpeg", "-ss", str(seek), "-i", str(path),
             "-vframes", "1", "-q:v", "3", "-y", tmp_path],
            capture_output=True, timeout=20
        )
        if result.returncode == 0 and Path(tmp_path).stat().st_size > 0:
            with open(tmp_path, "rb") as f:
                data = base64.b64encode(f.read()).decode("utf-8")
            os.unlink(tmp_path)
            return "image/jpeg", data
    except Exception:
        pass
    return None, None


def parse_response(text):
    text = text.strip()
    # Remove blocos markdown se o modelo os incluir
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break
    parsed = json.loads(text)
    descricao = str(parsed.get("descricao", "")).strip()
    tags = parsed.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    return descricao, json.dumps(tags, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Enriquece fotos com descrições via Gemini")
    parser.add_argument("--limit", type=int, default=1400,
                        help="Máximo de arquivos por execução (padrão: 1400)")
    parser.add_argument("--key", default=os.environ.get("GEMINI_API_KEY"),
                        help="Chave da API Gemini (ou use GEMINI_API_KEY no ambiente)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Segundos entre requisições (padrão: 1.0)")
    args = parser.parse_args()

    if not args.key:
        print("Erro: chave Gemini não encontrada.")
        print("Defina GEMINI_API_KEY no ambiente ou use --key SUA_CHAVE")
        print("Obtenha uma chave gratuita em: https://aistudio.google.com/apikey")
        sys.exit(1)

    client = genai.Client(api_key=args.key)

    conn = get_db()
    total_pendentes = conn.execute(
        "SELECT COUNT(*) FROM fotos WHERE descricao IS NULL"
    ).fetchone()[0]

    rows = conn.execute("""
        SELECT id, caminho, extensao, tipo FROM fotos
        WHERE descricao IS NULL
        ORDER BY id
        LIMIT ?
    """, (args.limit,)).fetchall()

    if not rows:
        print("Tudo já enriquecido! Nenhuma foto pendente.")
        conn.close()
        return

    print(f"\nPendentes no banco: {total_pendentes}")
    print(f"Processando agora:  {len(rows)} (limite: {args.limit})")
    print("Ctrl+C pausa — o progresso é salvo a cada item.\n")

    ok = erros = pulados = 0

    for i, row in enumerate(rows, 1):
        caminho = row["caminho"]
        nome = Path(caminho).name
        print(f"[{i}/{len(rows)}] {nome}", end=" ... ", flush=True)

        try:
            if not Path(caminho).exists():
                print("não encontrado, pulando.")
                pulados += 1
                continue

            ext = row["extensao"].lower()
            tipo = row["tipo"]

            if tipo == "video":
                mime, data = extract_video_frame(caminho)
                if not mime:
                    print("ffmpeg indisponível, pulando.")
                    pulados += 1
                    continue
            elif ext in PHOTO_EXTS:
                mime, data = encode_image(caminho)
            else:
                print("formato não suportado, pulando.")
                pulados += 1
                continue

            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=[
                    genai_types.Part.from_bytes(data=base64.b64decode(data), mime_type=mime),
                    PROMPT,
                ]
            )
            descricao, tags_json = parse_response(response.text)

            conn.execute(
                "UPDATE fotos SET descricao = ?, tags = ? WHERE id = ?",
                (descricao, tags_json, row["id"])
            )
            conn.commit()
            ok += 1
            print("OK")

        except KeyboardInterrupt:
            print(f"\n\nPausado. Enriquecidos: {ok} | Erros: {erros} | Pulados: {pulados}")
            restantes = total_pendentes - ok
            print(f"Restantes no banco: {restantes} — rode novamente para continuar.")
            conn.close()
            sys.exit(0)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                print(f"\nCota diária esgotada. Enriquecidos hoje: {ok}")
                print("Rode novamente amanhã — o progresso está salvo.")
                conn.close()
                sys.exit(0)
            erros += 1
            print(f"ERRO: {e}")
            time.sleep(3)
            continue

        time.sleep(args.delay)

    conn.close()

    restantes = total_pendentes - ok
    print(f"\nPronto! Enriquecidos: {ok} | Erros: {erros} | Pulados: {pulados}")
    if restantes > 0:
        print(f"Ainda pendentes: {restantes} — rode novamente amanhã.")
    else:
        print("Banco 100% enriquecido!")


if __name__ == "__main__":
    main()
