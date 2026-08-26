#!/usr/bin/env python3
"""
Migra descrições e tags do banco do Mac para o banco do ZimaOS.
Cruza por nome de arquivo — não depende de caminhos iguais.

Uso:
    python3 migrate_descriptions.py --source fotos_mac.db --target fotos.db

O --source é o banco do Mac (copiado para o ZimaOS).
O --target é o banco recém-indexado no ZimaOS.
"""
import argparse
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="fotos.db do Mac")
    parser.add_argument("--target", required=True, help="fotos.db do ZimaOS")
    args = parser.parse_args()

    src = sqlite3.connect(args.source)
    src.row_factory = sqlite3.Row

    tgt = sqlite3.connect(args.target)

    # Carregar todas as descrições do Mac indexadas por nome de arquivo
    mac_rows = src.execute(
        "SELECT nome, descricao, tags FROM fotos WHERE descricao IS NOT NULL"
    ).fetchall()
    mac_map = {r["nome"]: (r["descricao"], r["tags"]) for r in mac_rows}

    print(f"Descrições disponíveis no Mac: {len(mac_map)}")

    # Buscar arquivos sem descrição no ZimaOS
    pendentes = tgt.execute(
        "SELECT id, nome FROM fotos WHERE descricao IS NULL"
    ).fetchall()
    print(f"Arquivos sem descrição no ZimaOS: {len(pendentes)}")

    ok = nao_encontrado = 0
    for row in pendentes:
        if row[1] in mac_map:
            descricao, tags = mac_map[row[1]]
            tgt.execute(
                "UPDATE fotos SET descricao = ?, tags = ? WHERE id = ?",
                (descricao, tags, row[0])
            )
            ok += 1
        else:
            nao_encontrado += 1

    tgt.commit()
    src.close()
    tgt.close()

    print(f"\nMigrados: {ok}")
    print(f"Sem correspondência (novos no ZimaOS): {nao_encontrado}")
    print(f"Esses {nao_encontrado} serão enriquecidos pelo enricher normalmente.")


if __name__ == "__main__":
    main()
