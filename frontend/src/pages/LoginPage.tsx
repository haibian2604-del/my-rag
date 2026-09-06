import { useEffect, useState, type FormEvent } from "react";
import { ApiError, get, post } from "../api/client";

type Mode = "login" | "register";

export default function LoginPage({ onUnlocked }: { onUnlocked: () => void }) {
  const [checking, setChecking] = useState(true);
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    get<{ registered: boolean }>("/api/auth/status")
      .then((s) => setMode(s.registered ? "login" : "register"))
      .catch((e) => setError(e instanceof ApiError ? e.message : "无法连接服务，请重试"))
      .finally(() => setChecking(false));
  }, []);

  const switchMode = (next: Mode) => {
    if (mode === next) return;
    setMode(next);
    setError("");
    setConfirm("");
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setError("");
    if (mode === "register" && password !== confirm) {
      setError("两次输入的密码不一致");
      return;
    }
    setBusy(true);
    try {
      if (mode === "login") {
        await post("/api/auth/login", { username, password });
      } else {
        try {
          await post("/api/auth/register", { username, password });
        } catch (err) {
          // 已在别处注册过：展示原因并切回登录表单
          if (err instanceof ApiError && err.status === 403) {
            switchMode("login");
            setError(err.message);
            return;
          }
          throw err;
        }
      }
      onUnlocked();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-paper px-4">
      <form onSubmit={submit} className="panel w-full max-w-sm p-7">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-md bg-seal font-display text-lg text-white">
            知
          </span>
          <span className="font-display text-2xl">知笥</span>
        </div>
        {checking ? (
          <p className="mt-3 text-sm text-faint">正在检查账号状态…</p>
        ) : (
          <>
            {/* 登录 / 注册切换页签 */}
            <div className="mt-5 grid grid-cols-2 rounded-md bg-paper p-1 text-sm">
              {(["login", "register"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  className={`rounded-sm px-3 py-1.5 transition-colors ${
                    mode === m
                      ? "bg-card font-medium text-ink shadow-sm"
                      : "text-faint hover:text-ink"
                  }`}
                  onClick={() => switchMode(m)}
                >
                  {m === "login" ? "登录" : "注册"}
                </button>
              ))}
            </div>
            {mode === "login" ? (
              <>
                <p className="mt-4 text-sm text-faint">请输入账号密码登录。</p>
                <input
                  type="text"
                  autoFocus
                  autoCapitalize="none"
                  autoCorrect="off"
                  className="input mt-4"
                  placeholder="用户名"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
                <input
                  type="password"
                  className="input mt-3"
                  placeholder="密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </>
            ) : (
              <>
                <p className="mt-4 text-sm text-faint">首次使用，请先创建管理员账号。</p>
                <input
                  type="text"
                  autoFocus
                  autoCapitalize="none"
                  autoCorrect="off"
                  className="input mt-4"
                  placeholder="用户名（字母、数字、_ 或 -）"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
                <input
                  type="password"
                  className="input mt-3"
                  placeholder="密码（至少 6 位）"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <input
                  type="password"
                  className="input mt-3"
                  placeholder="确认密码"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </>
            )}
            {error && <p className="mt-2 text-sm text-seal">{error}</p>}
            <button
              type="submit"
              className="btn-primary mt-5 w-full"
              disabled={busy || !username || !password || (mode === "register" && !confirm)}
            >
              {busy ? "提交中…" : mode === "login" ? "登录" : "创建账号"}
            </button>
          </>
        )}
      </form>
    </div>
  );
}
