import { useEffect, useState } from "react";
import { ApiError, get, put, type Workspace } from "../api/client";
import EmbeddingSwitcher from "../components/EmbeddingSwitcher";
import ProviderForm, { type Provider } from "../components/ProviderForm";

const KINDS = [
  { key: "llm", label: "LLM", hint: "用来生成回答的模型。本地 oMLX 一般是 http://localhost:19723/v1（容器内用 http://host.docker.internal:19723/v1）。" },
  { key: "embedding", label: "嵌入", hint: "把文档和问题转成向量用于检索。中文场景推荐多语言模型（如 bge-m3）。" },
  { key: "rerank", label: "重排", hint: "可选。对检索结果做二次排序，M3 版本支持。" },
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
  const [appMsgError, setAppMsgError] = useState(false);
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
    setAppMsgError(false);
    try {
      if (authEnabled && !password) {
        setAppMsg("开启访问密码需要先设置密码");
        setAppMsgError(true);
        return;
      }
      await put("/api/settings/app", {
        auth_enabled: authEnabled,
        password: authEnabled ? password : undefined,
      });
      setPassword("");
      await refreshApp();
      setAppMsg(authEnabled ? "已开启访问密码" : "已关闭访问密码");
    } catch (e) {
      setAppMsg(e instanceof ApiError ? e.message : "保存失败");
      setAppMsgError(true);
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
  const activeKind = KINDS.find((k) => k.key === tab)!;

  return (
    <div className="mx-auto h-full max-w-2xl overflow-y-auto px-4 py-6">
      <h1 className="font-display text-lg">设置</h1>

      <section className="mt-6">
        <div className="flex gap-1 border-b border-line pb-3">
          {KINDS.map((k) => (
            <button
              key={k.key}
              className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                tab === k.key
                  ? "bg-iblue-soft font-medium text-iblue"
                  : "text-faint hover:text-ink"
              }`}
              onClick={() => setTab(k.key)}
            >
              {k.label}
            </button>
          ))}
        </div>
        <p className="mt-3 text-xs leading-5 text-faint">{activeKind.hint}</p>
        <div className="mt-3">
          <ProviderForm
            key={`${tab}-${currentProvider?.id ?? "new"}`}
            kind={tab}
            provider={currentProvider}
            onSaved={() => void refreshProviders()}
          />
          {tab === "embedding" && (
            <div className="mt-4">
              <EmbeddingSwitcher />
            </div>
          )}
        </div>
      </section>

      <section className="mt-8 border-t border-line pt-6">
        <h2 className="font-display text-base">检索参数</h2>
        <p className="mt-1 text-xs text-faint">控制每次回答时如何从文档中取材，改动只影响当前工作区。</p>
        {wsSettings && (
          <>
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label className="block text-sm">
                <span className="mb-1 block">每次检索的段落数（1–20）</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  className="input"
                  value={wsSettings.top_k}
                  onChange={(e) => setWsSettings({ ...wsSettings, top_k: Number(e.target.value) })}
                />
                <span className="mt-1 block text-xs text-faint">取多少段最相关的原文给模型参考</span>
              </label>
              <label className="block text-sm">
                <span className="mb-1 block">相似度阈值（0–1）</span>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  className="input"
                  value={wsSettings.score_threshold}
                  onChange={(e) =>
                    setWsSettings({ ...wsSettings, score_threshold: Number(e.target.value) })
                  }
                />
                <span className="mt-1 block text-xs text-faint">低于这个相似度的段落直接丢弃，0 表示不过滤</span>
              </label>
              <label className="block text-sm">
                <span className="mb-1 block">单次回答的资料上限（500–8000 tokens）</span>
                <input
                  type="number"
                  min={500}
                  max={8000}
                  step={100}
                  className="input"
                  value={wsSettings.context_max_tokens}
                  onChange={(e) =>
                    setWsSettings({ ...wsSettings, context_max_tokens: Number(e.target.value) })
                  }
                />
                <span className="mt-1 block text-xs text-faint">一次回答最多带入多少原文，太大时回答会变慢</span>
              </label>
              <label className="flex items-start gap-2 pt-7 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={wsSettings.use_rerank}
                  onChange={(e) => setWsSettings({ ...wsSettings, use_rerank: e.target.checked })}
                />
                <span>
                  启用重排
                  <span className="mt-0.5 block text-xs text-faint">对召回结果做二次排序（当前为简化实现）</span>
                </span>
              </label>
            </div>
            <div className="mt-4 flex items-center gap-3">
              <button className="btn-primary" onClick={() => void saveWs()}>
                保存
              </button>
              {wsMsg && <span className="text-sm text-faint">{wsMsg}</span>}
            </div>
          </>
        )}
      </section>

      <section className="mt-8 border-t border-line pt-6">
        <h2 className="font-display text-base">访问密码</h2>
        <p className="mt-1 text-xs leading-5 text-faint">
          默认仅本机可访问、无需密码。要在局域网里用其他设备访问时建议开启；
          开启前请确保服务端已用环境变量设置强随机的 JWT 与加密密钥。
        </p>
        <label className="mt-4 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={appSettings.auth_enabled}
            onChange={(e) => void saveApp(e.target.checked)}
          />
          启用访问密码
        </label>
        {appSettings.auth_enabled && (
          <div className="mt-3 flex items-center gap-2 text-sm">
            <input
              type="password"
              className="input max-w-56"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="设置新密码"
            />
            <button className="btn-ghost" onClick={() => void saveApp(true)}>
              更新密码
            </button>
          </div>
        )}
        {appMsg && (
          <p className={`mt-2 text-sm ${appMsgError ? "text-seal" : "text-faint"}`}>{appMsg}</p>
        )}
      </section>
    </div>
  );
}
