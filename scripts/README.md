# 备份 / 恢复脚本

## 用法

```bash
# 备份（默认输出到 ../backups，可用第一参数指定目录）
./scripts/backup.sh [输出目录]

# 恢复（危险：pg_restore --clean 会覆盖目标库现有对象，针对 rag 主库执行前务必确认！）
./scripts/restore.sh <backup-dir>

# 非交互（自动化）跳过确认
SKIP_CONFIRM=1 ./scripts/restore.sh <backup-dir>
```

连接参数可用环境变量覆盖：`PG_CONTAINER`（默认 pgvector）、`PG_USER`（默认 postgres）、`PG_DB`（默认 rag）。

产物结构：`<输出目录>/backup-YYYYmmdd-HHMMSS/`，含 `db.dump`（pg_dump -Fc）与 `storage.tar.gz`（仓库根 storage/ 快照，目录不存在则跳过）。

## 演练步骤（推荐先在临时数据库上做，勿直接覆盖开发库）

1. **备份**：`./scripts/backup.sh`，记下产物目录。
2. **记录文档数**：
   ```bash
   docker exec pgvector psql -U postgres -d rag -c \
     "SELECT (SELECT count(*) FROM documents) AS docs, (SELECT count(*) FROM chunks) AS chunks;"
   ```
3. **制造变更**（可选，验证可恢复性）：上传/删除一篇文档，再次 `./scripts/backup.sh` 得到第二份备份。
4. **恢复演练（临时库方式，不动 rag 主库）**：
   ```bash
   docker exec pgvector psql -U postgres -c "CREATE DATABASE rag_restore_test"
   docker exec -i pgvector pg_restore --clean --if-exists \
     -U postgres -d rag_restore_test < <backup-dir>/db.dump
   # 比对计数（应与第 2 步一致）
   docker exec pgvector psql -U postgres -d rag_restore_test -c \
     "SELECT (SELECT count(*) FROM documents) AS docs, (SELECT count(*) FROM chunks) AS chunks;"
   docker exec pgvector psql -U postgres -c "DROP DATABASE rag_restore_test"
   ```
5. **真机恢复**（确需时）：`SKIP_CONFIRM=1 ./scripts/restore.sh <backup-dir>`，恢复完成后重启后端，登录验证文档与对话仍在。

storage 快照由 restore.sh 解包到仓库根；解包前会把当前 storage/ 改名为 `storage.bak-<时间戳>` 留底。
