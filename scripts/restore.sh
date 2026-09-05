#!/usr/bin/env bash
# 恢复脚本：从 backup.sh 的产物恢复数据库与 storage/。
#
# ⚠️ 危险操作：pg_restore --clean 会先 DROP 目标库中的现有对象。
#    针对 rag 主库执行前务必确认！建议先在临时数据库上演练
#    （见 scripts/README.md 演练一节）。
#
# 用法: ./restore.sh <backup-dir>   （含 db.dump / storage.tar.gz 的目录）
# 环境变量: SKIP_CONFIRM=1 跳过交互确认；PG_CONTAINER/PG_USER/PG_DB 同 backup.sh
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "用法: $0 <backup-dir>" >&2
  exit 1
fi

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

BACKUP_DIR="$1"
DUMP="${BACKUP_DIR}/db.dump"
STORAGE_TAR="${BACKUP_DIR}/storage.tar.gz"
PG_CONTAINER="${PG_CONTAINER:-pgvector}"
PG_USER="${PG_USER:-postgres}"
PG_DB="${PG_DB:-rag}"
TS="$(date +%Y%m%d-%H%M%S)"

[ -f "$DUMP" ] || { echo "错误：未找到 ${DUMP}" >&2; exit 1; }

echo "即将恢复："
echo "  数据库: ${PG_USER}@${PG_CONTAINER}/${PG_DB}（--clean --if-exists，将覆盖现有对象！）"
echo "  dump:   ${DUMP}"
[ -f "$STORAGE_TAR" ] && echo "  storage: ${STORAGE_TAR}"

if [ "${SKIP_CONFIRM:-0}" != "1" ]; then
  read -p "确认继续？输入 yes 继续: " ans
  [ "$ans" = "yes" ] || { echo "已取消"; exit 1; }
fi

echo "==> [1/3] 恢复数据库"
if ! docker exec -i "${PG_CONTAINER}" pg_restore --clean --if-exists -U "${PG_USER}" -d "${PG_DB}" < "$DUMP"; then
  echo "错误：pg_restore 失败" >&2
  exit 1
fi

if [ -f "$STORAGE_TAR" ]; then
  echo "==> [2/3] 备份当前 storage/ 为 storage.bak-${TS}"
  if [ -d "${REPO_ROOT}/storage" ]; then
    mv "${REPO_ROOT}/storage" "${REPO_ROOT}/storage.bak-${TS}"
  fi
  echo "==> [3/3] 解包 storage/ 到仓库根"
  tar xzf "$STORAGE_TAR" -C "${REPO_ROOT}"
else
  echo "==> [2/3] 无 storage.tar.gz，跳过"
  echo "==> [3/3] 跳过"
fi

echo "恢复完成。请重启后端服务使连接与新数据生效。"
