import { useState, type FormEvent } from "react";
import { ApiError, post } from "../api/client";

export default function LoginGate({ onUnlocked }: { onUnlocked: () => void }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError("");
    try {
      await post("/api/auth/login", { password });
      onUnlocked();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "登录失败，请重试");
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
        <p className="mt-3 text-sm text-faint">此知识库已开启访问密码，请输入后继续。</p>
        <input
          type="password"
          autoFocus
          className="input mt-5"
          placeholder="访问密码"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="mt-2 text-sm text-seal">{error}</p>}
        <button type="submit" className="btn-primary mt-5 w-full" disabled={busy || !password}>
          {busy ? "验证中…" : "解锁"}
        </button>
      </form>
    </div>
  );
}
