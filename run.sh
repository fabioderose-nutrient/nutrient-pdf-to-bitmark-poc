#!/bin/sh
# PDF to bitmark demo: starts the local server on http://localhost:8082
# Empty NUTRIENT_LICENSE_KEY / NUTRIENT_WEB_LICENSE_KEY run the Nutrient SDKs in evaluation mode.
# Optional, for the learning checks: OPENAI_API_KEY, ANTHROPIC_API_KEY or a local Ollama (see README.md).
cd "$(dirname "$0")" || exit 1
if [ -f .env ]; then set -a; . ./.env; set +a; fi
cd backend || exit 1
if [ ! -d node_modules ]; then npm install; fi
exec python3 -m uvicorn app:app --port "${PORT:-8082}"
