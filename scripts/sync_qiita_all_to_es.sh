#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${BASE_DIR}"

ES_URL="${ES_URL:-http://elastic1:9200}"
INDEX="${INDEX:-qiita-articles}"
QIITA_USER="${QIITA_USER:-naritomo08}"

MARKDOWN_DIR="tmp/qiita_markdown"

mkdir -p logs tmp

LOG_FILE="logs/sync_qiita_all_to_es.log"

#
# 前回ログを退避
#
if [ -f "${LOG_FILE}" ]; then
  mv -f "${LOG_FILE}" "${LOG_FILE}.1"
fi

#
# ログ初期化
#
: > "${LOG_FILE}"

log() {
  echo "[$(date '+%F %T')] $*"
}

{
  log "=================================================="
  log "Qiita sync start"
  log "ES_URL=${ES_URL}"
  log "INDEX=${INDEX}"
  log "USER=${QIITA_USER}"

  #
  # Markdownディレクトリ再生成
  #
  rm -rf "${MARKDOWN_DIR}"
  mkdir -p "${MARKDOWN_DIR}"

  log "fetch qiita articles"

  python3 scripts/fetch_qiita_articles.py \
    --user "${QIITA_USER}" \
    --markdown-dir "${MARKDOWN_DIR}"

  ARTICLE_COUNT=$(find "${MARKDOWN_DIR}" -name "*.md" | wc -l)

  log "markdown files=${ARTICLE_COUNT}"

  if [ "${ARTICLE_COUNT}" -eq 0 ]; then
    log "ERROR: no markdown files fetched"
    exit 1
  fi

  log "sync markdown to elasticsearch"

  python3 scripts/import_qiita_markdown_to_es.py \
    --markdown-dir "${MARKDOWN_DIR}" \
    --es-url "${ES_URL}" \
    --index "${INDEX}" \
    --user "${QIITA_USER}" \
    --delete-missing

  COUNT=$(curl -s \
    "${ES_URL}/${INDEX}/_count" \
    | jq -r '.count')

  log "elasticsearch document count=${COUNT}"

  log "Qiita sync completed"
  log "=================================================="

} >> "${LOG_FILE}" 2>&1

echo "sync completed"
echo "log: ${LOG_FILE}"