"use client";

import { motion } from "framer-motion";
import { Loader2, LockKeyhole } from "lucide-react";
import { type FormEvent, useState } from "react";
import { api, ApiError, setUnlockToken } from "@/lib/api";

/** Shown on every new visit (every new tab), for every user, until the dashboard password is entered.
 *  On the very first visit (no password exists yet) the authorized owner creates it here. */
export function LockScreen({ email, onUnlocked, setup = false, needsCode = false, minLength = 12 }: {
  email: string | null; onUnlocked: () => void; setup?: boolean; needsCode?: boolean; minLength?: number }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (setup && password.length < minLength) return setError(`Use at least ${minLength} characters.`);
    if (setup && password !== confirm) return setError("The two passwords don't match.");
    setBusy(true);
    try {
      const r = await api<{ token: string | null }>(setup ? "/v1/session/setup" : "/v1/session/unlock", { method: "POST", body: setup && needsCode ? { password, code: code.trim() } : { password } });
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
        <h1 id="lock-title" className="mt-5 text-lg font-semibold">{setup ? "Create the dashboard password" : "CLIPX AI is locked"}</h1>
        <p className="mt-1 text-sm text-muted">
          {setup
            ? <>First visit: choose the password everyone will need on every visit (at least {minLength} characters). Only its hash is stored.</>
            : <>Enter the dashboard password to continue{email && email !== "owner" ? <> as <span className="text-fg-2">{email}</span></> : null}.</>}
        </p>
        {setup && needsCode && (
          <label className="mt-6 block">
            <span className="mb-1.5 block text-xs font-medium text-fg-2">Setup code</span>
            <input type="text" autoComplete="off" spellCheck={false} required value={code} onChange={(e) => setCode(e.target.value)}
              className="h-10 w-full rounded-lg border border-line-strong bg-[#0b0b0d] px-3 font-mono text-sm text-fg focus:border-[#7c3aed]/70 focus:outline-none" />
            <span className="mt-1 block text-[11px] text-muted">The one-time code you received when the site was set up. It stops working once the password exists.</span>
          </label>
        )}
        <label className={setup && needsCode ? "mt-4 block" : "mt-6 block"}>
          <span className="mb-1.5 block text-xs font-medium text-fg-2">Password</span>
          <input
            type="password"
            autoComplete={setup ? "new-password" : "current-password"}
            minLength={setup ? minLength : undefined}
            autoFocus
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="h-10 w-full rounded-lg border border-line-strong bg-[#0b0b0d] px-3 text-sm text-fg focus:border-[#7c3aed]/70 focus:outline-none"
          />
        </label>
        {setup && (
          <label className="mt-4 block">
            <span className="mb-1.5 block text-xs font-medium text-fg-2">Repeat password</span>
            <input type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)}
              className="h-10 w-full rounded-lg border border-line-strong bg-[#0b0b0d] px-3 text-sm text-fg focus:border-[#7c3aed]/70 focus:outline-none" />
          </label>
        )}
        {error && <p role="alert" className="mt-3 text-sm text-[#f08a8a]">{error}</p>}
        <button
          type="submit"
          disabled={busy || !password}
          className="accent-gradient mt-5 inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg text-sm font-medium text-white disabled:opacity-50"
        >
          {busy && <Loader2 size={15} className="animate-spin" aria-hidden />} {setup ? "Create password" : "Unlock"}
        </button>
        <p className="mt-4 text-xs text-muted">
          You will be asked again in every new tab and whenever you lock the dashboard.
        </p>
      </motion.form>
    </div>
  );
}
