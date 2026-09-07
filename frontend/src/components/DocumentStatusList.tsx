import { useState } from "react";
import { get } from "../api/client";

export interface DocumentItem {
  id: number;
  workspace_id: number;
  filename: string;
  source_type: string;
  mime: string;
  size: number;
  checksum: string;
  status: "pending" | "parsing" | "embedding" | "ready" | "failed";
  error: string | null;
  summary: string | null;
}

const STATUS_META: Record<
  DocumentItem["status"],
  { label: string; dot: string }
> = {
  pending: { label: "排队中", dot: "bg-faint/40" },
  parsing: { label: "解析中", dot: "bg-iblue dot-live" },
  embedding: { label: "向量化中", dot: "bg-iblue dot-live" },
  ready: { label: "可问答", dot: "bg-ok" },
  failed: { label: "失败", dot: "bg-seal" },
};

function extBadge(filename: string): string {
  const m = filename.match(/\.([a-z0-9]+)$/i);
  return (m?.[1] ?? "?").toUpperCase().slice(0, 4);
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface DocDetail {
  summary: string | null;
  preview: string;
}

export default function DocumentStatusList({
  documents,
  onDelete,
  onReingest,
  onGenSummary,
  busyId,
}: {
  documents: DocumentItem[];
  onDelete: (doc: DocumentItem) => void;
  onReingest: (doc: DocumentItem) => void;
  onGenSummary: (doc: DocumentItem) => void;
  busyId: number | null;
}) {
  const [openErrorId, setOpenErrorId] = useState<number | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [detail, setDetail] = useState<Record<number, DocDetail>>({});

  // 展开/收起详情区：首次展开时拉取详情（preview），失败静默（不显示预览）
  const toggleDetail = async (docId: number) => {
    if (detailId === docId) {
      setDetailId(null);
      return;
    }
    setDetailId(docId);
    if (!detail[docId]) {
      try {
        const d = await get<DocDetail>(`/api/documents/${docId}`);
        setDetail((prev) => ({ ...prev, [docId]: d }));
      } catch {
        /* 详情拉取失败静默，仅不显示预览 */
      }
    }
  };

  if (documents.length === 0) {
    return (
      <p className="py-10 text-center text-sm text-faint">
        还没有文档。上传第一份文件后，就能向它提问了。
      </p>
    );
  }

  const readyCount = documents.filter((d) => d.status === "ready").length;
  const processingCount = documents.filter(
    (d) => d.status === "pending" || d.status === "parsing" || d.status === "embedding",
  ).length;
  const failedCount = documents.filter((d) => d.status === "failed").length;

  return (
    <div>
      <p className="px-1 pb-2 text-xs text-faint">
        {readyCount} 篇可问答 · {processingCount} 处理中 · {failedCount} 失败
      </p>
      <ul className="divide-y divide-line rounded-lg border border-line bg-card">
      {documents.map((doc) => {
        const meta = STATUS_META[doc.status] ?? STATUS_META.pending;
        return (
          <li key={doc.id} className="px-4 py-3">
            <div className="flex items-center gap-3">
              <span className="w-11 shrink-0 rounded bg-paper-deep px-1 py-0.5 text-center font-mono text-[10px] text-faint">
                {extBadge(doc.filename)}
              </span>
              <span className="min-w-0 flex-1 truncate text-sm" title={doc.filename}>
                {doc.filename}
              </span>
              <span className="shrink-0 text-xs text-faint">{formatSize(doc.size)}</span>
              <span className="flex w-24 shrink-0 items-center gap-1.5 text-xs">
                <span className={`dot ${meta.dot}`} />
                {meta.label}
              </span>
              <div className="flex shrink-0 gap-1">
                <button
                  className="rounded px-2 py-1 text-xs text-iblue hover:bg-iblue-soft disabled:opacity-40"
                  disabled={busyId === doc.id}
                  onClick={() => void toggleDetail(doc.id)}
                >
                  详情
                </button>
                <button
                  className="rounded px-2 py-1 text-xs text-iblue hover:bg-iblue-soft disabled:opacity-40"
                  disabled={busyId === doc.id}
                  onClick={() => onGenSummary(doc)}
                >
                  {doc.summary ? "重写摘要" : "生成摘要"}
                </button>
                <button
                  className="rounded px-2 py-1 text-xs text-iblue hover:bg-iblue-soft disabled:opacity-40"
                  disabled={busyId === doc.id}
                  onClick={() => onReingest(doc)}
                >
                  重新嵌入
                </button>
                <button
                  className="rounded px-2 py-1 text-xs text-seal hover:bg-seal-soft disabled:opacity-40"
                  disabled={busyId === doc.id}
                  onClick={() => onDelete(doc)}
                >
                  删除
                </button>
              </div>
            </div>
            {/* 摘要：有则显示，无则不占位 */}
            {doc.summary && (
              <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-faint" title={doc.summary}>
                摘要：{doc.summary}
              </p>
            )}
            {detailId === doc.id && (
              <div className="mt-2 rounded-md bg-paper-deep px-3 py-2 text-xs leading-5">
                <p className="text-faint">摘要</p>
                <p className="mt-0.5 text-ink">{detail[doc.id]?.summary || doc.summary || "（未生成）"}</p>
                <p className="mt-2 text-faint">原文预览（前 500 字符）</p>
                <pre className="mt-0.5 max-h-40 overflow-y-auto whitespace-pre-wrap break-all text-faint">
                  {detail[doc.id]?.preview || "（无法读取原文）"}
                </pre>
              </div>
            )}
            {doc.status === "failed" && doc.error && (
              <div className="mt-2 rounded-md bg-seal-soft px-3 py-2 text-xs leading-5 text-seal">
                <p className={openErrorId === doc.id ? "" : "line-clamp-2"}>{doc.error}</p>
                {doc.error.length > 80 && (
                  <button
                    className="mt-1 underline underline-offset-2"
                    onClick={() => setOpenErrorId(openErrorId === doc.id ? null : doc.id)}
                  >
                    {openErrorId === doc.id ? "收起" : "展开全部"}
                  </button>
                )}
                <button
                  className="ml-3 underline underline-offset-2"
                  disabled={busyId === doc.id}
                  onClick={() => onReingest(doc)}
                >
                  重试
                </button>
              </div>
            )}
          </li>
        );
      })}
      </ul>
    </div>
  );
}
