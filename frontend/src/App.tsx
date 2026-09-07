import { useCallback, useEffect, useState } from "react";
import ConfirmDialog from "./components/ConfirmDialog";
import { ApiError, del, ensureDefaultWorkspace, get, post, type Conversation, type Workspace } from "./api/client";
import ChatPage from "./pages/ChatPage";
import DocumentsPage from "./pages/DocumentsPage";
import SettingsPage from "./pages/SettingsPage";
import LoginPage from "./pages/LoginPage";
import WorkspaceSwitcher from "./components/WorkspaceSwitcher";

type Page = "chat" | "documents" | "settings";

const NAV: { key: Page; label: string }[] = [
  { key: "chat", label: "对话" },
  { key: "documents", label: "文档库" },
  { key: "settings", label: "设置" },
];

function BrandMark({ className = "" }: { className?: string }) {
  return (
    <span
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-seal font-display text-base text-white ${className}`}
    >
      知
    </span>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("chat");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  // 激活会话 ID 提升到 App：站内切页返回时恢复离开的会话
  const [activeConvId, setActiveConvId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [needsAuth, setNeedsAuth] = useState(false);
  const [username, setUsername] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [delConv, setDelConv] = useState<Conversation | null>(null);

  const fetchConversations = useCallback(async (wsId: number) => {
    const list = await get<Conversation[]>(`/api/workspaces/${wsId}/conversations`);
    setConversations(list);
    return list;
  }, []);

  const init = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const ws = await ensureDefaultWorkspace();
      setWorkspace(ws);
      void fetchConversations(ws.id).catch(() => undefined);
      // 展示登录用户名；失败不阻塞主界面
      get<{ username: string }>("/api/auth/me")
        .then((u) => setUsername(u.username))
        .catch(() => setUsername(""));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setNeedsAuth(true);
      } else {
        setError(e instanceof ApiError ? e.message : "初始化失败");
      }
    } finally {
      setLoading(false);
    }
  }, [fetchConversations]);

  useEffect(() => {
    void init();
  }, [init]);

  useEffect(() => {
    const onUnauthorized = () => setNeedsAuth(true);
    window.addEventListener("rag:unauthorized", onUnauthorized);
    return () => window.removeEventListener("rag:unauthorized", onUnauthorized);
  }, []);

  const logout = useCallback(async () => {
    try {
      await post("/api/auth/logout");
    } catch {
      // Cookie 已失效时后端也会失败，直接回登录页即可
    }
    setWorkspace(null);
    setActiveConvId(null);
    setConversations([]);
    setPage("chat");
    setUsername("");
    setNeedsAuth(true);
  }, []);

  // 切换到不同工作区时清空激活会话并加载该工作区的会话列表
  const switchWorkspace = (ws: Workspace) => {
    if (workspace && ws.id !== workspace.id) {
      setActiveConvId(null);
      setConversations([]);
      void fetchConversations(ws.id).catch(() => undefined);
    }
    setWorkspace(ws);
  };

  const newConversation = async () => {
    if (!workspace) return;
    try {
      const conv = await post<Conversation>(`/api/workspaces/${workspace.id}/conversations`);
      await fetchConversations(workspace.id);
      setActiveConvId(conv.id);
      setPage("chat");
    } catch {
      // 创建失败静默：用户可重试
    }
  };

  const removeConversation = async (conv: Conversation) => {
    setDelConv(null);
    try {
      await del(`/api/conversations/${conv.id}`);
      if (!workspace) return;
      await fetchConversations(workspace.id);
      if (activeConvId === conv.id) setActiveConvId(null);
    } catch {
      // 删除失败静默
    }
  };

  if (needsAuth) {
    return (
      <LoginPage
        onUnlocked={() => {
          setNeedsAuth(false);
          void init();
        }}
      />
    );
  }

  const navButtons = () =>
    NAV.map((n) => (
      <button
        key={n.key}
        className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
          page === n.key ? "bg-iblue-soft font-medium text-iblue" : "text-faint hover:text-ink"
        }`}
        onClick={() => setPage(n.key)}
      >
        {n.label}
      </button>
    ));

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="sticky top-0 flex h-screen shrink-0 flex-col overflow-hidden border-r border-line bg-card max-md:hidden">
        <div className="flex items-center gap-2.5 px-4 pb-5 pt-5">
          <BrandMark />
          <div>
            <div className="font-display text-lg leading-tight">知笥</div>
            <div className="text-xs text-faint">个人知识库</div>
          </div>
        </div>
        <nav className="flex flex-col gap-0.5 px-2">{navButtons()}</nav>
        {workspace && (
          <WorkspaceSwitcher workspace={workspace} onSwitch={switchWorkspace} />
        )}
        {workspace && (
          <div className="flex min-h-0 flex-1 flex-col px-2 pb-1">
            <div className="flex items-center justify-between px-2 pb-1 pt-2">
              <span className="text-xs text-faint">会话</span>
              <button
                className="text-xs text-faint transition-colors hover:text-ink"
                onClick={() => void newConversation()}
              >
                ＋新建
              </button>
            </div>
            <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
              {conversations.length === 0 && (
                <li className="px-2 py-1 text-xs text-faint">暂无会话</li>
              )}
              {conversations.map((c) => (
                <li key={c.id} className="group relative">
                  <button
                    className={`w-full truncate rounded-md px-2 py-1.5 pr-7 text-left text-sm transition-colors ${
                      page === "chat" && activeConvId === c.id
                        ? "bg-iblue-soft font-medium text-iblue"
                        : "text-ink hover:bg-paper"
                    }`}
                    onClick={() => {
                      setActiveConvId(c.id);
                      setPage("chat");
                    }}
                    title={c.title || `会话 #${c.id}`}
                  >
                    {c.title || `会话 #${c.id}`}
                  </button>
                  <button
                    className="absolute right-1.5 top-1.5 hidden text-xs text-seal group-hover:block"
                    onClick={() => setDelConv(c)}
                    title="删除会话"
                  >
                    删除
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-auto flex items-center gap-2.5 border-t border-line px-4 py-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-seal font-display text-sm uppercase text-white">
            {(username[0] ?? "?").toUpperCase()}
          </span>
          <span className="min-w-0 flex-1 truncate text-sm font-medium">
            {username || "已登录"}
          </span>
          <button
            title="退出登录"
            aria-label="退出登录"
            className="shrink-0 rounded-md p-1.5 text-faint transition-colors hover:bg-paper hover:text-seal"
            onClick={() => void logout()}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" x2="9" y1="12" y2="12" />
            </svg>
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-line bg-card px-3 py-2 md:hidden">
          <BrandMark />
          <span className="font-display text-base">知笥</span>
          <nav className="ml-auto flex gap-0.5">{navButtons()}</nav>
        </header>

        <main className="min-h-0 flex-1">
          {loading && <p className="py-16 text-center text-sm text-faint">正在打开书箧…</p>}
          {!loading && error && (
            <div className="mx-auto max-w-md py-16 text-center">
              <p className="text-sm text-seal">{error}</p>
              <button className="btn-ghost mt-3" onClick={() => void init()}>
                重试
              </button>
            </div>
          )}
          {!loading && !error && workspace && (
            <>
              {page === "chat" && (
                <ChatPage
                  workspace={workspace}
                  activeId={activeConvId}
                  onActiveChange={setActiveConvId}
                  conversations={conversations}
                  onConversationsChange={setConversations}
                  onRefreshConversations={fetchConversations}
                />
              )}
              {page === "documents" && <DocumentsPage workspace={workspace} />}
              {page === "settings" && <SettingsPage workspace={workspace} />}
            </>
          )}
        </main>
      </div>

      <ConfirmDialog
        open={delConv !== null}
        title="删除会话"
        message={
          delConv
            ? `删除会话${delConv.title && delConv.title !== "新对话" ? `「${delConv.title}」` : ""}？其中的问答记录将一并删除，不可恢复。`
            : ""
        }
        confirmText="删除"
        danger
        onConfirm={() => delConv && void removeConversation(delConv)}
        onCancel={() => setDelConv(null)}
      />
    </div>
  );
}
