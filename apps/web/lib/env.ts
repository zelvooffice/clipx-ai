/** Server-side configuration. Values come from Worker vars/secrets (never bundled into client code). */
export function env(name: string): string {
  const v = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env?.[name];
  return v ?? "";
}

export const isProduction = () => env("NODE_ENV") === "production" || env("CLIPX_ENV") === "production";
