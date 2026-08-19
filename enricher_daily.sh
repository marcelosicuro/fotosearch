#!/bin/bash
ENV_FILE="$HOME/Projetos/foto-search/.env"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

if [ -n "$GEMINI_API_KEY" ] && ! pgrep -f "enricher.py" > /dev/null; then
    /Library/Developer/CommandLineTools/usr/bin/python3 "$HOME/Projetos/foto-search/enricher.py" \
        --limit 1400 --key "$GEMINI_API_KEY"
fi
