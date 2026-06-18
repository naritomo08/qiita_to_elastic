#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${BASE_DIR}"

ES_URL="${ES_URL:-http://elastic1:9200}"
INDEX="${INDEX:-qiita-articles}"
QIITA_USER="${QIITA_USER:-naritomo08}"

mkdir -p tmp logs

LOG_FILE="logs/sync_qiita_all_to_es.log"

{
  echo "[$(date '+%F %T')] sync start"

  rm -rf tmp/qiita_markdown
  mkdir -p tmp/qiita_markdown

  python3 scripts/fetch_qiita_articles.py \
    --user "${QIITA_USER}" \
    --markdown-dir tmp/qiita_markdown

  python3 scripts/import_qiita_markdown_to_es.py \
    --markdown-dir tmp/qiita_markdown \
    --es-url "${ES_URL}" \
    --index "${INDEX}" \
    --user "${QIITA_USER}" \
    --delete-missing

  echo "[$(date '+%F %T')] sync completed"
} >> "${LOG_FILE}" 2>&1
