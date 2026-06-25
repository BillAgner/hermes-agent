#!/usr/bin/env bash
# Start self-hosted Honcho for Hermes Agent.
#
# This is a multi-service stack (API + deriver + PostgreSQL + Redis),
# not a single image. The official path is to clone the Honcho repo,
# copy the example docker-compose, and build the API image locally.
#
# Usage:
#   bash scripts/start_honcho.sh          # start
#   bash scripts/start_honcho.sh stop     # stop
#   bash scripts/start_honcho.sh logs     # tail logs
#   bash scripts/start_honcho.sh status   # health check
#   bash scripts/start_honcho.sh clean    # stop and remove volumes (DESTROYS DATA)
#
# After it's up, point Hermes at it:
#   hermes memory setup honcho      # select "self-hosted", URL http://localhost:8000
#
# Reference: https://docs.honcho.dev/v3/contributing/self-hosting

set -euo pipefail

HONCHO_DIR="${HONCHO_HOME:-$HOME/.honcho}"
HONCHO_PORT="${HONCHO_PORT:-8000}"
COMPOSE_FILE="$HONCHO_DIR/docker-compose.yml"
ENV_FILE="$HONCHO_DIR/.env"

ACTION="${1:-start}"

case "$ACTION" in
  start)
    if [ ! -d "$HONCHO_DIR" ]; then
      echo "▶ Cloning Honcho into $HONCHO_DIR ..."
      git clone https://github.com/plastic-labs/honcho.git "$HONCHO_DIR"
    fi
    cd "$HONCHO_DIR"
    if [ ! -f "$COMPOSE_FILE" ]; then
      cp docker-compose.yml.example docker-compose.yml
    fi
    if [ ! -f "$ENV_FILE" ]; then
      cp .env.template .env
      echo "▶ Created $ENV_FILE. You MUST set an LLM provider or the"
      echo "  Honcho API will refuse to start. Easiest: point at Ollama"
      echo "  (any OpenAI-compatible endpoint works)."
      echo
      echo "  Add these lines to $ENV_FILE:"
      echo "    LLM_OPENAI_API_KEY=ollama"
      echo "    MODEL_CONFIG__MODEL=qwen2.5:7b"
      echo "    MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1"
      echo "    EMBEDDING_MODEL_CONFIG__MODEL=bge-m3"
      echo "    EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1"
      echo
      echo "  Then re-run this script."
      exit 1
    fi
    echo "▶ Starting Honcho stack (api + database + redis) ..."
    docker compose up -d --build
    echo
    echo "▶ Waiting for API health ..."
    for i in {1..30}; do
      if curl -sf "http://localhost:${HONCHO_PORT}/health" > /dev/null 2>&1; then
        echo "  ✓ Honcho API is healthy on http://localhost:${HONCHO_PORT}"
        echo
        echo "Next: hermes memory setup honcho  (select self-hosted, URL above)"
        exit 0
      fi
      sleep 2
    done
    echo "  ✗ API did not become healthy in 60s. Check: docker compose logs api"
    exit 1
    ;;

  stop)
    cd "$HONCHO_DIR" 2>/dev/null && docker compose down || echo "No honcho dir at $HONCHO_DIR"
    ;;

  logs)
    cd "$HONCHO_DIR" 2>/dev/null && docker compose logs -f --tail=100
    ;;

  status)
    if curl -sf "http://localhost:${HONCHO_PORT}/health" > /dev/null 2>&1; then
      echo "✓ Honcho API is healthy on http://localhost:${HONCHO_PORT}"
      curl -s "http://localhost:${HONCHO_PORT}/health"
    else
      echo "✗ Honcho API is not reachable on http://localhost:${HONCHO_PORT}"
      exit 1
    fi
    ;;

  clean)
    cd "$HONCHO_DIR" 2>/dev/null && docker compose down -v || echo "No honcho dir at $HONCHO_DIR"
    echo "Honcho volumes removed. Data is gone."
    ;;

  *)
    echo "Usage: $0 {start|stop|logs|status|clean}"
    exit 1
    ;;
esac
