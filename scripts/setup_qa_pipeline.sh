#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

load_env_if_unset() {
  local env_file="$1"
  [[ -f "$env_file" ]] || return 0
  while IFS= read -r raw_line; do
    local line="${raw_line#"${raw_line%%[![:space:]]*}"}"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    [[ "$line" == *"="* ]] || continue
    local key="${line%%=*}"
    local value="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ ${#value} -ge 2 ]]; then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi
    if [[ -z "${!key+x}" ]]; then
      export "$key=$value"
    fi
  done < "$env_file"
}

load_env_if_unset ".env.local"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is not set (.env.local)." >&2
  exit 1
fi

if [[ -z "${GEMINI_API_KEY:-}" && -z "${GOOGLE_API_KEY:-}" ]]; then
  echo "ERROR: GEMINI_API_KEY (or GOOGLE_API_KEY) is not set (.env.local)." >&2
  exit 1
fi

EMBEDDINGS_JSONL="${1:-data/embeddings/textbook_full_gemini_v1.jsonl}"

if [[ ! -f "$EMBEDDINGS_JSONL" ]]; then
  echo "ERROR: Embeddings file not found: $EMBEDDINGS_JSONL" >&2
  echo "Build it first with scripts/build_embeddings.py, then rerun." >&2
  exit 1
fi

echo "Applying schema..."
psql "$DATABASE_URL" -f sql/core_schema.sql

echo "Loading embeddings from $EMBEDDINGS_JSONL ..."
python scripts/load_textbook_embeddings.py \
  --db-url "$DATABASE_URL" \
  --jsonl "$EMBEDDINGS_JSONL"

echo
echo "Pipeline ready."
echo "Ask a question with:"
echo "  ./scripts/ask_question.sh \"What is Newton's second law?\""
