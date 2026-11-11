"use client";

import { motion } from "framer-motion";
import { Loader2, LockKeyhole } from "lucide-react";
import { type FormEvent, useState } from "react";
import { api, ApiError, setUnlockToken } from "@/lib/api";

/** Shown on every new visit (every new tab), for every user, until the dashboard password is entered. */
export function LockScreen({ email, onUnlocked }: { email: string | null; onUnlocked: () => void }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ token: string | null }>("/v1/session/unlock", { method: "POST", body: { password } });
      setUnlockToken(r.token);
      setPassword("");
      onUnlocked();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-glow grid min-h-screen place-items-center px-4">
      <motion.form
        onSubmit={submit}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-sm rounded-2xl border border-line bg-card p-7 shadow-2xl"
        aria-labelledby="lock-title"
      >
        <span className="accent-gradient grid size-11 place-items-center rounded-xl text-white">
          <LockKeyhole size={20} aria-hidden />
        </span>
        <h1 id="lock-title" className="mt-5 text-lg font-semibold">CLIPX AI is locked</h1>
        <p className="mt-1 text-sm text-muted">
          Enter the dashboard password to continue{email ? <> as <span className="text-fg-2">{email}</span></> : null}.
        </p>
        <label className="mt-6 block">
          <span className="mb-1.5 block text-xs font-medium text-fg-2">Password</span>
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="h-10 w-full rounded-lg border border-line-strong bg-[#0b0b0d] px-3 text-sm text-fg focus:border-[#7c3aed]/70 focus:outline-none"
          />
        </label>
        {error && <p role="alert" className="mt-3 text-sm text-[#f08a8a]">{error}</p>}
        <button
          type="submit"
          disabled={busy || !password}
          className="accent-gradient mt-5 inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg text-sm font-medium text-white disabled:opacity-50"
        >
          {busy && <Loader2 size={15} className="animate-spin" aria-hidden />} Unlock
        </button>
        <p className="mt-4 text-xs text-muted">
          You will be asked again in every new tab and whenever you lock the dashboard.
        </p>
      </motion.form>
    </div>
  );
}
