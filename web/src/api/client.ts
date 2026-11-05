import type { components } from "./schema";

type S = components["schemas"];
export type Me = S["MeOut"];
export type User = S["UserOut"];
export type TenantProfile = S["TenantProfile"];
export type AddressCandidate = S["AddressCandidate"];
export type PropertyCreate = S["PropertyCreate"];
export type PropertyUpdate = S["PropertyUpdate"];
export type Property = S["PropertyOut"];
export type Unit = S["UnitOut"];
export type Ownership = S["OwnershipOut"];
export type Verification = S["VerificationOut"];
export type QueueItem = S["QueueItem"];
export type VerificationCase = S["VerificationCase"];

export type ApiErrorKind = "no_api" | "unauthorized" | "network" | "server" | "unavailable" | "domain";

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
  const isForm = body instanceof FormData;
  let response: Response;
  try {
    response = await fetch(API_URL + path, {
      method,
      headers: {
        Authorization: `tma ${initData}`,
        // Безкоштовний ngrok інакше віддає HTML-попередження замість відповіді API.
        "ngrok-skip-browser-warning": "1",
        // Для FormData заголовок із boundary ставить сам браузер.
        ...(body === undefined || isForm ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiFailure("network");
  }
  if (response.status === 401) throw new ApiFailure("unauthorized");
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const err = data as { error?: string; field?: string } | null;
    if (response.status === 503) throw new ApiFailure("unavailable", err?.error);
    if (err?.error) throw new ApiFailure("domain", err.error, err.field);
    if (response.status === 422) throw new ApiFailure("domain", "invalid_input");
    throw new ApiFailure("server");
  }
  return data as T;
}

const p = (id: string) => `/api/v1/properties/${encodeURIComponent(id)}`;
const v = (id: string) => `/api/v1/moderation/ownership/${encodeURIComponent(id)}`;

export const api = {
  me: () => request<Me>("GET", "/api/v1/me"),
  register: (body: S["RegisterIn"]) => request<User>("POST", "/api/v1/register", body),
  rename: (display_name: string) => request<User>("PATCH", "/api/v1/me", { display_name }),
  deleteAccount: () => request<void>("DELETE", "/api/v1/me"),
  tenantProfile: () => request<TenantProfile>("GET", "/api/v1/me/tenant-profile"),
  saveTenantProfile: (body: TenantProfile) =>
    request<TenantProfile>("PUT", "/api/v1/me/tenant-profile", body),

  // Об'єкти (M2)
  searchAddress: (q: string) =>
    request<AddressCandidate[]>("GET", `/api/v1/addresses?q=${encodeURIComponent(q)}`),
  properties: () => request<Property[]>("GET", "/api/v1/properties"),
  createProperty: (body: PropertyCreate) => request<Property>("POST", "/api/v1/properties", body),
  property: (id: string) => request<Property>("GET", p(id)),
  updateProperty: (id: string, body: PropertyUpdate) => request<Property>("PUT", p(id), body),
  deleteProperty: (id: string) => request<void>("DELETE", p(id)),
  addRoom: (id: string, body: S["RoomIn"]) => request<Property>("POST", `${p(id)}/rooms`, body),
  removeRoom: (id: string, unitId: string) =>
    request<Property>("DELETE", `${p(id)}/rooms/${encodeURIComponent(unitId)}`),

  // Власність
  ownership: (id: string) => request<Ownership>("GET", `${p(id)}/ownership`),
  uploadDocument: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<Ownership>("POST", `${p(id)}/ownership/documents`, form);
  },
  removeDocument: (id: string, docId: string) =>
    request<Ownership>("DELETE", `${p(id)}/ownership/documents/${encodeURIComponent(docId)}`),
  submitOwnership: (id: string) => request<Ownership>("POST", `${p(id)}/ownership/submit`),
  devVerify: (id: string) => request<Ownership>("POST", `${p(id)}/ownership/dev-verify`),

  // Модерація
  moderationSummary: () => request<S["ModerationSummaryOut"]>("GET", "/api/v1/moderation/summary"),
  ownershipQueue: () => request<QueueItem[]>("GET", "/api/v1/moderation/ownership"),
  ownershipCase: (vid: string) => request<VerificationCase>("GET", v(vid)),
  sendDocuments: (vid: string) => request<S["SentOut"]>("POST", `${v(vid)}/send-documents`),
  approveOwnership: (vid: string, owner_name: string) =>
    request<VerificationCase>("POST", `${v(vid)}/approve`, { owner_name }),
  rejectOwnership: (vid: string, reason: string) =>
    request<VerificationCase>("POST", `${v(vid)}/reject`, { reason }),
};
