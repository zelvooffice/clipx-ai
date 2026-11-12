/** Server-side configuration. Values come from Worker vars/secrets (never bundled into client code). */
export function env(name: string): string {
  const v = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env?.[name];
  return v ?? "";
}

/** Fail closed: only an explicit CLIPX_ENV=development enables the local dev bypass. */
export const isProduction = () => env("CLIPX_ENV") !== "development";
