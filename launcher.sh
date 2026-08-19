#!/bin/bash
# Carrega chave da API
ENV_FILE="$HOME/Projetos/foto-search/.env"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

# Inicia o servidor se não estiver rodando
if ! lsof -i :5050 | grep -q LISTEN; then
    /usr/bin/python3 "$HOME/Projetos/foto-search/server.py" >> /tmp/fotosearch_server.log 2>&1 &
    sleep 1
fi

# Roda o enricher em background se a chave estiver definida e ele não estiver ativo
if [ -n "$GEMINI_API_KEY" ] && ! pgrep -f "enricher.py" > /dev/null; then
    /usr/bin/python3 "$HOME/Projetos/foto-search/enricher.py" \
        --limit 1400 --key "$GEMINI_API_KEY" \
        >> /tmp/fotosearch_enricher.log 2>&1 &
fi
