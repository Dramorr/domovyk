import type { components } from "./schema";

type S = components["schemas"];
export type Me = S["MeOut"];
export type User = S["UserOut"];
export type TenantProfile = S["TenantProfile"];

export type ApiErrorKind = "no_api" | "unauthorized" | "network" | "server" | "domain";

export class ApiFailure extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    /** Код доменної помилки з бекенду (`banned`, `invalid_input`, …). */
    public readonly code?: string,
    public readonly field?: string,
  ) {
    super(code ?? kind);
  }
}

// Значення-заглушка (напр. TODO у змінних GitHub) = API ще не налаштовано.
const RAW_API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "";
const API_URL = /^https?:\/\/[^\s/]+/.test(RAW_API_URL) ? RAW_API_URL.replace(/\/$/, "") : "";

export function apiConfigured(): boolean {
  return API_URL !== "";
}

let initData = "";

export function setInitData(value: string): void {
  initData = value;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (!apiConfigured()) throw new ApiFailure("no_api");
  let response: Response;
  try {
    response = await fetch(API_URL + path, {
      method,
      headers: {
        Authorization: `tma ${initData}`,
        // Безкоштовний ngrok інакше віддає HTML-попередження замість відповіді API.
        "ngrok-skip-browser-warning": "1",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiFailure("network");
  }
  if (response.status === 401) throw new ApiFailure("unauthorized");
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const err = data as { error?: string; field?: string } | null;
    if (err?.error) throw new ApiFailure("domain", err.error, err.field);
    if (response.status === 422) throw new ApiFailure("domain", "invalid_input");
    throw new ApiFailure("server");
  }
  return data as T;
}

export const api = {
  me: () => request<Me>("GET", "/api/v1/me"),
  register: (body: S["RegisterIn"]) => request<User>("POST", "/api/v1/register", body),
  rename: (display_name: string) => request<User>("PATCH", "/api/v1/me", { display_name }),
  deleteAccount: () => request<void>("DELETE", "/api/v1/me"),
  tenantProfile: () => request<TenantProfile>("GET", "/api/v1/me/tenant-profile"),
  saveTenantProfile: (body: TenantProfile) =>
    request<TenantProfile>("PUT", "/api/v1/me/tenant-profile", body),
};
