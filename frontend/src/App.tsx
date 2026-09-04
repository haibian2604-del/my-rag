import { useEffect, useState } from "react";
import { ApiError, ensureDefaultWorkspace, type Workspace } from "./api/client";
import ChatPage from "./pages/ChatPage";
import DocumentsPage from "./pages/DocumentsPage";
import SettingsPage from "./pages/SettingsPage";

type Page = "chat" | "documents" | "settings";

const NAV: { key: Page; label: string }[] = [
  { key: "chat", label: "对话" },
  { key: "documents", label: "文档库" },
  { key: "settings", label: "设置" },
];

export default function App() {
  const [page, setPage] = useState<Page>("chat");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    ensureDefaultWorkspace()
      .then(setWorkspace)
      .catch((e) => setError(e instanceof ApiError ? e.message : "初始化失败"));
  }, []);

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <header className="flex items-center gap-6 border-b border-gray-200 bg-white px-6 py-3">
        <span className="text-sm font-bold">My RAG</span>
        <nav className="flex gap-1">
          {NAV.map((n) => (
            <button
              key={n.key}
              className={`rounded px-3 py-1.5 text-sm ${
                page === n.key
                  ? "bg-blue-600 text-white"
                  : "text-gray-700 hover:bg-gray-100"
              }`}
              onClick={() => setPage(n.key)}
            >
              {n.label}
            </button>
          ))}
        </nav>
        {workspace && <span className="ml-auto text-xs text-gray-400">工作区：{workspace.name}</span>}
      </header>
      <main className="p-6">
        {error && <p className="mx-auto max-w-3xl text-sm text-red-600">{error}</p>}
        {!workspace && !error && <p className="text-center text-sm text-gray-400">加载中…</p>}
        {workspace && (
          <>
            {page === "chat" && <ChatPage workspace={workspace} />}
            {page === "documents" && <DocumentsPage workspace={workspace} />}
            {page === "settings" && <SettingsPage workspace={workspace} />}
          </>
        )}
      </main>
    </div>
  );
}
