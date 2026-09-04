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

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div
        className={`flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-10 text-center transition-colors ${
          dragOver ? "border-blue-500 bg-blue-50" : "border-gray-300 bg-white"
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
        <p className="text-sm text-gray-600">拖拽文件到此处，或</p>
        <label className="mt-2 cursor-pointer rounded bg-blue-600 px-4 py-1.5 text-sm text-white hover:bg-blue-700">
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
        <p className="mt-2 text-xs text-gray-400">支持 .md / .txt / .pdf / .docx，单个不超过 50MB</p>
      </div>
      {uploadError && <p className="text-sm text-red-600">{uploadError}</p>}
      <DocumentStatusList documents={documents} onDelete={remove} onReingest={reingest} busyId={busyId} />
    </div>
  );
}
