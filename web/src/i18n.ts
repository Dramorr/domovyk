import uk from "./locales/uk.json";

export type TextKey = keyof typeof uk;

export function t(key: TextKey, vars: Record<string, string> = {}): string {
  return uk[key].replace(/\{(\w+)\}/g, (_, name: string) => vars[name] ?? "");
}
