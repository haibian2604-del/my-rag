import { useEffect, useState } from "react";
import { ApiError, get, put, type Workspace } from "../api/client";
import ProviderForm, { type Provider } from "../components/ProviderForm";

const KINDS = [
  { key: "llm", label: "LLM" },
  { key: "embedding", label: "嵌入" },
  { key: "rerank", label: "重排" },
] as const;

interface WorkspaceSettings {
  top_k: number;
  score_threshold: number;
  use_rerank: boolean;
  context_max_tokens: number;
}

export default function SettingsPage({ workspace }: { workspace: Workspace }) {
  const [tab, setTab] = useState<(typeof KINDS)[number]["key"]>("llm");
  const [providers, setProviders] = useState<Provider[]>([]);
  const [appSettings, setAppSettings] = useState<{ auth_enabled: boolean }>({ auth_enabled: false });
  const [password, setPassword] = useState("");
  const [appMsg, setAppMsg] = useState("");
  const [wsSettings, setWsSettings] = useState<WorkspaceSettings | null>(null);
  const [wsMsg, setWsMsg] = useState("");

  const refreshProviders = async () => {
    setProviders(await get<Provider[]>("/api/settings/providers"));
  };
  const refreshApp = async () => {
    setAppSettings(await get<{ auth_enabled: boolean }>("/api/settings/app"));
  };
  const refreshWs = async () => {
    setWsSettings(await get<WorkspaceSettings>(`/api/workspaces/${workspace.id}/settings`));
  };

  useEffect(() => {
    void refreshProviders().catch(() => undefined);
    void refreshApp().catch(() => undefined);
    void refreshWs().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  const saveApp = async (authEnabled: boolean) => {
    setAppMsg("");
    try {
      if (authEnabled && !password) {
        setAppMsg("开启认证时必须设置密码");
        return;
      }
      await put("/api/settings/app", {
        auth_enabled: authEnabled,
        password: authEnabled ? password : undefined,
      });
      setPassword("");
      await refreshApp();
      setAppMsg(authEnabled ? "已开启密码认证" : "已关闭密码认证");
    } catch (e) {
      setAppMsg(e instanceof ApiError ? e.message : "保存失败");
    }
  };

  const saveWs = async () => {
    if (!wsSettings) return;
    setWsMsg("");
    try {
      await put(`/api/workspaces/${workspace.id}/settings`, wsSettings);
      setWsMsg("已保存");
    } catch (e) {
      setWsMsg(e instanceof ApiError ? e.message : "保存失败");
    }
  };

  const currentProvider = providers.find((p) => p.kind === tab) ?? null;

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <section className="space-y-3">
        <div className="flex gap-2">
          {KINDS.map((k) => (
            <button
              key={k.key}
              className={`rounded px-3 py-1.5 text-sm ${
                tab === k.key
                  ? "bg-blue-600 text-white"
                  : "border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
              }`}
              onClick={() => setTab(k.key)}
            >
              {k.label}
            </button>
          ))}
        </div>
        <ProviderForm
          key={`${tab}-${currentProvider?.id ?? "new"}`}
          kind={tab}
          provider={currentProvider}
          onSaved={() => void refreshProviders()}
        />
      </section>

      <section className="space-y-3 rounded border border-gray-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-gray-800">应用设置</h3>
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={appSettings.auth_enabled}
            onChange={(e) => void saveApp(e.target.checked)}
          />
          启用访问密码
        </label>
        {appSettings.auth_enabled && (
          <div className="flex items-center gap-2 text-sm">
            <input
              type="password"
              className="rounded border border-gray-300 px-2 py-1.5"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="新密码"
            />
            <button
              className="rounded border border-gray-300 px-3 py-1.5 hover:bg-gray-50"
              onClick={() => void saveApp(true)}
            >
              更新密码
            </button>
          </div>
        )}
        {appMsg && <p className="text-sm text-gray-600">{appMsg}</p>}
      </section>

      {wsSettings && (
        <section className="space-y-3 rounded border border-gray-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-gray-800">检索参数</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="mb-1 block text-gray-600">Top K（1-20）</span>
              <input
                type="number"
                min={1}
                max={20}
                className="w-full rounded border border-gray-300 px-2 py-1.5"
                value={wsSettings.top_k}
                onChange={(e) => setWsSettings({ ...wsSettings, top_k: Number(e.target.value) })}
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-gray-600">分数阈值（0-1）</span>
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                className="w-full rounded border border-gray-300 px-2 py-1.5"
                value={wsSettings.score_threshold}
                onChange={(e) =>
                  setWsSettings({ ...wsSettings, score_threshold: Number(e.target.value) })
                }
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-gray-600">上下文最大 tokens（500-8000）</span>
              <input
                type="number"
                min={500}
                max={8000}
                step={100}
                className="w-full rounded border border-gray-300 px-2 py-1.5"
                value={wsSettings.context_max_tokens}
                onChange={(e) =>
                  setWsSettings({ ...wsSettings, context_max_tokens: Number(e.target.value) })
                }
              />
            </label>
            <label className="flex items-center gap-2 pt-6 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={wsSettings.use_rerank}
                onChange={(e) => setWsSettings({ ...wsSettings, use_rerank: e.target.checked })}
              />
              启用重排
            </label>
          </div>
          <div className="flex items-center gap-3">
            <button
              className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
              onClick={() => void saveWs()}
            >
              保存
            </button>
            {wsMsg && <span className="text-sm text-gray-600">{wsMsg}</span>}
          </div>
        </section>
      )}
    </div>
  );
}
