#!/bin/bash
PROJ="$HOME/Projetos/foto-search"
PYTHON="/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python"
ENV_FILE="$PROJ/.env"

if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

# 1. Indexa novos arquivos adicionados à pasta desde o último login
$PYTHON "$PROJ/indexer.py" >> /tmp/fotosearch_watcher.log 2>&1

# 2. Enriquece com IA os arquivos ainda sem descrição
if [ -n "$GEMINI_API_KEY" ] && ! pgrep -f "enricher.py" > /dev/null; then
    caffeinate -i $PYTHON "$PROJ/enricher.py" \
        --limit 1400 --key "$GEMINI_API_KEY" >> /tmp/fotosearch_watcher.log 2>&1
fi
