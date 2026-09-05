#!/usr/bin/env bash
# 备份脚本：pg_dump 数据库 + storage/ 目录快照。
# 用法: ./backup.sh [输出目录]   （默认 ../backups，相对仓库根）
# 环境变量: PG_CONTAINER=pgvector  PG_USER=postgres  PG_DB=rag
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

PG_CONTAINER="${PG_CONTAINER:-pgvector}"
PG_USER="${PG_USER:-postgres}"
PG_DB="${PG_DB:-rag}"
OUT_DIR="${1:-${REPO_ROOT}/../backups}"
TS="$(date +%Y%m%d-%H%M%S)"
DEST="${OUT_DIR}/backup-${TS}"

mkdir -p "$DEST"

echo "==> [1/3] 导出数据库 ${PG_DB}（容器 ${PG_CONTAINER}，pg_dump -Fc）"
if ! docker exec "${PG_CONTAINER}" pg_dump -Fc -U "${PG_USER}" "${PG_DB}" > "${DEST}/db.dump"; then
  echo "错误：pg_dump 失败（docker exec ${PG_CONTAINER}）" >&2
  exit 1
fi

echo "==> [2/3] 打包 storage/ 目录快照"
if [ -d "${REPO_ROOT}/storage" ]; then
  tar czf "${DEST}/storage.tar.gz" -C "${REPO_ROOT}" storage
else
  echo "  storage/ 不存在，跳过"
fi

echo "==> [3/3] 备份产物"
ls -lh "${DEST}"
echo "备份完成: ${DEST}"
