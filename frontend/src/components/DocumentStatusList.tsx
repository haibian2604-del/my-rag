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
}

const STATUS_META: Record<DocumentItem["status"], { label: string; cls: string }> = {
  pending: { label: "等待中", cls: "bg-gray-100 text-gray-600" },
  parsing: { label: "解析中", cls: "bg-blue-100 text-blue-700" },
  embedding: { label: "向量化中", cls: "bg-amber-100 text-amber-700" },
  ready: { label: "就绪", cls: "bg-green-100 text-green-700" },
  failed: { label: "失败", cls: "bg-red-100 text-red-700" },
};

export default function DocumentStatusList({
  documents,
  onDelete,
  onReingest,
  busyId,
}: {
  documents: DocumentItem[];
  onDelete: (doc: DocumentItem) => void;
  onReingest: (doc: DocumentItem) => void;
  busyId: number | null;
}) {
  if (documents.length === 0) {
    return <p className="py-8 text-center text-sm text-gray-400">还没有文档，先上传一些吧</p>;
  }
  return (
    <ul className="divide-y divide-gray-100 rounded border border-gray-200 bg-white">
      {documents.map((doc) => {
        const meta = STATUS_META[doc.status] ?? STATUS_META.pending;
        return (
          <li key={doc.id} className="flex items-center gap-3 px-4 py-2.5">
            <span className="min-w-0 flex-1 truncate text-sm text-gray-900" title={doc.filename}>
              {doc.filename}
            </span>
            <span className="text-xs text-gray-400">{(doc.size / 1024).toFixed(1)} KB</span>
            <span
              className={`rounded px-2 py-0.5 text-xs font-medium ${meta.cls}`}
              title={doc.error ?? undefined}
            >
              {meta.label}
              {doc.status === "failed" && doc.error ? `（悬停查看错误）` : ""}
            </span>
            <button
              className="rounded px-2 py-1 text-xs text-blue-600 hover:bg-blue-50 disabled:opacity-40"
              disabled={busyId === doc.id}
              onClick={() => onReingest(doc)}
            >
              重新嵌入
            </button>
            <button
              className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50 disabled:opacity-40"
              disabled={busyId === doc.id}
              onClick={() => onDelete(doc)}
            >
              删除
            </button>
          </li>
        );
      })}
    </ul>
  );
}
