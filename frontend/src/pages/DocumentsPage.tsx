import { useEffect, useState } from "react";
import { ApiError, del, get } from "../api/client";
import DocumentStatusList, { type DocumentItem } from "../components/DocumentStatusList";
import { post, uploadFiles, type Workspace } from "../api/client";

export default function DocumentsPage({ workspace }: { workspace: Workspace }) {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [url, setUrl] = useState("");
  const [fetching, setFetching] = useState(false);

  const refresh = async () => {
    try {
      setDocuments(await get<DocumentItem[]>(`/api/workspaces/${workspace.id}/documents`));
    } catch {
      /* 轮询失败静默，下轮重试 */
    }
  };

  useEffect(() => {
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  const handleFiles = async (files: FileList | File[]) => {
    setUploadError("");
    setUploading(true);
    try {
      for (const f of Array.from(files)) {
        await uploadFiles(workspace.id, [f]);
      }
      await refresh();
    } catch (e) {
      setUploadError(e instanceof ApiError ? e.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const fetchUrl = async () => {
    const trimmed = url.trim();
    if (!trimmed || fetching) return;
    setUploadError("");
    setFetching(true);
    try {
      await post(`/api/workspaces/${workspace.id}/documents/url`, { url: trimmed });
      setUrl("");
      await refresh();
    } catch (e) {
      setUploadError(e instanceof ApiError ? e.message : "抓取失败");
    } finally {
      setFetching(false);
    }
  };

  const remove = async (doc: DocumentItem) => {
    setBusyId(doc.id);
    try {
      await del(`/api/documents/${doc.id}`);
      await refresh();
    } catch (e) {
      setUploadError(e instanceof ApiError ? e.message : "删除失败");
    } finally {
      setBusyId(null);
    }
  };

  const reingest = async (doc: DocumentItem) => {
    setBusyId(doc.id);
    try {
      await post(`/api/documents/${doc.id}/reingest`);
      await refresh();
    } catch (e) {
      setUploadError(e instanceof ApiError ? e.message : "重新嵌入失败");
    } finally {
      setBusyId(null);
    }
  };

  const genSummary = async (doc: DocumentItem) => {
    setBusyId(doc.id);
    try {
      await post(`/api/documents/${doc.id}/summary`);
      await refresh();
    } catch (e) {
      setUploadError(e instanceof ApiError ? e.message : "摘要生成失败");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-4 py-6">
      <h1 className="font-display text-lg">文档库</h1>
      <p className="mt-1 text-sm text-faint">上传后的文档会自动解析、切分并向量化，完成后即可在对话中问答。</p>

      <div
        className={`mt-5 flex items-center justify-center gap-3 rounded-lg border border-dashed px-6 py-7 text-center transition-colors ${
          dragOver ? "border-iblue bg-iblue-soft" : "border-line bg-card"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files.length > 0) void handleFiles(e.dataTransfer.files);
        }}
      >
        <p className="text-sm text-faint">拖入文件，或</p>
        <label className="btn-ghost cursor-pointer">
          {uploading ? "上传中…" : "选择文件"}
          <input
            type="file"
            multiple
            className="hidden"
            accept=".md,.txt,.pdf,.docx"
            disabled={uploading}
            onChange={(e) => {
              if (e.target.files?.length) void handleFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <input
          type="text"
          className="input flex-1"
          placeholder="https://example.com/article"
          value={url}
          disabled={fetching}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void fetchUrl();
          }}
        />
        <button type="button" className="btn-primary" disabled={fetching || !url.trim()} onClick={() => void fetchUrl()}>
          {fetching ? "抓取中…" : "抓取网页"}
        </button>
      </div>
      <p className="mt-2 text-center text-xs text-faint">支持 .md / .txt / .pdf / .docx，单个不超过 50MB</p>

      {uploadError && <p className="mt-3 text-sm text-seal">{uploadError}</p>}

      <div className="mt-6">
        <DocumentStatusList documents={documents} onDelete={remove} onReingest={reingest} onGenSummary={genSummary} busyId={busyId} />
      </div>
      </div>
    </div>
  );
}
