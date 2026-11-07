import type { components } from "./schema";

type S = components["schemas"];
export type Me = S["MeOut"];
export type User = S["UserOut"];
export type Mode = S["AppMode"];
export type TenantProfile = S["TenantProfile"];
export type AddressCandidate = S["AddressCandidate"];
export type City = S["CityOut"];
export type PropertyCreate = S["PropertyCreate"];
export type PropertyUpdate = S["PropertyUpdate"];
export type PropertyDetails = S["PropertyDetails-Input"];
export type Property = S["PropertyOut"];
export type Unit = S["UnitOut"];
export type Ownership = S["OwnershipOut"];
export type Verification = S["VerificationOut"];
export type QueueItem = S["QueueItem"];
export type Lease = S["LeaseOut"];
export type LeaseSummary = S["LeaseSummary"];
export type LeaseCreate = S["LeaseCreate"];
export type LeaseConditions = S["LeaseConditions"];
export type LeaseTerms = S["LeaseTerms"];
export type Invite = S["InviteOut"];
export type InvitePreview = S["InvitePreview"];
export type Message = S["MessageOut"];
export type MessagesPage = S["MessagesPage"];
export type NotifyPrefs = S["LeaseNotificationPrefs"];
export type VerificationCase = S["VerificationCase"];
export type ContractState = S["ContractState"];
export type ContractParty = S["PartyOut"];
export type SignatureCase = S["SignatureCase"];

export type ApiErrorKind = "no_api" | "unauthorized" | "network" | "server" | "unavailable" | "domain";

export class ApiFailure extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    /** Код доменної помилки з бекенду (`banned`, `invalid_input`, …). */
    public readonly code?: string,
    public readonly field?: string,
    /** Уточнення доменної помилки (напр. чому запрошення недійсне). */
    public readonly reason?: string,
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
    const err = data as { error?: string; field?: string; reason?: string } | null;
    if (response.status === 503) throw new ApiFailure("unavailable", err?.error);
    if (err?.error) throw new ApiFailure("domain", err.error, err.field, err.reason);
    if (response.status === 422) throw new ApiFailure("domain", "invalid_input");
    throw new ApiFailure("server");
  }
  return data as T;
}

const p = (id: string) => `/api/v1/properties/${encodeURIComponent(id)}`;
const l = (id: string) => `/api/v1/leases/${encodeURIComponent(id)}`;
const c = (id: string) => `/api/v1/contracts/${encodeURIComponent(id)}`;
const v = (id: string) => `/api/v1/moderation/ownership/${encodeURIComponent(id)}`;

export const api = {
  me: () => request<Me>("GET", "/api/v1/me"),
  register: (body: S["RegisterIn"]) => request<User>("POST", "/api/v1/register", body),
  rename: (display_name: string) => request<User>("PATCH", "/api/v1/me", { display_name }),
  setMode: (mode: Mode) => request<User>("PUT", "/api/v1/me/mode", { mode }),
  deleteAccount: () => request<void>("DELETE", "/api/v1/me"),
  tenantProfile: () => request<TenantProfile>("GET", "/api/v1/me/tenant-profile"),
  saveTenantProfile: (body: TenantProfile) =>
    request<TenantProfile>("PUT", "/api/v1/me/tenant-profile", body),

  // Об'єкти (M2)
  cities: () => request<City[]>("GET", "/api/v1/cities"),
  searchAddress: (q: string, city?: string) =>
    request<AddressCandidate[]>(
      "GET",
      `/api/v1/addresses?q=${encodeURIComponent(q)}${city ? `&city=${encodeURIComponent(city)}` : ""}`,
    ),
  properties: () => request<Property[]>("GET", "/api/v1/properties"),
  createProperty: (body: PropertyCreate) => request<Property>("POST", "/api/v1/properties", body),
  property: (id: string) => request<Property>("GET", p(id)),
  updateProperty: (id: string, body: PropertyUpdate) => request<Property>("PUT", p(id), body),
  deleteProperty: (id: string) => request<void>("DELETE", p(id)),
  updateProfile: (id: string, body: PropertyDetails) => request<Property>("PUT", `${p(id)}/details`, body),
  addRoom: (id: string, body: S["RoomIn"]) => request<Property>("POST", `${p(id)}/rooms`, body),
  removeRoom: (id: string, unitId: string) =>
    request<Property>("DELETE", `${p(id)}/rooms/${encodeURIComponent(unitId)}`),

  // Власність
  ownership: (id: string) => request<Ownership>("GET", `${p(id)}/ownership`),
  /** Файл уже прочитаний у пам'ять (див. readFile у Properties.tsx). */
  uploadDocument: (id: string, data: Blob, filename: string) => {
    const form = new FormData();
    form.append("file", data, filename);
    return request<Ownership>("POST", `${p(id)}/ownership/documents`, form);
  },
  removeDocument: (id: string, docId: string) =>
    request<Ownership>("DELETE", `${p(id)}/ownership/documents/${encodeURIComponent(docId)}`),
  submitOwnership: (id: string) => request<Ownership>("POST", `${p(id)}/ownership/submit`),
  devVerify: (id: string) => request<Ownership>("POST", `${p(id)}/ownership/dev-verify`),

  // Договори (M4)
  contract: (leaseId: string) => request<ContractState>("GET", `${l(leaseId)}/contract`),
  setLegalName: (leaseId: string, full_name: string) =>
    request<ContractState>("PUT", `${l(leaseId)}/contract/name`, { full_name }),
  generateContract: (leaseId: string) => request<ContractState>("POST", `${l(leaseId)}/contract/generate`),
  uploadExternal: (leaseId: string, data: Blob, filename: string) => {
    const form = new FormData();
    form.append("file", data, filename);
    return request<ContractState>("POST", `${l(leaseId)}/contract/external`, form);
  },
  uploadSignature: (contractId: string, data: Blob, filename: string) => {
    const form = new FormData();
    form.append("file", data, filename);
    return request<ContractState>("POST", `${c(contractId)}/signature`, form);
  },
  decideExternal: (contractId: string, accept: boolean) =>
    request<ContractState>("POST", `${c(contractId)}/external-decision`, { accept }),
  sendContract: (contractId: string, what: "pdf" | "package") =>
    request<S["SentOut"]>("POST", `${c(contractId)}/send`, { what }),
  signatureQueue: () => request<SignatureCase[]>("GET", "/api/v1/moderation/signatures"),
  signatureCase: (id: string) => request<SignatureCase>("GET", `/api/v1/moderation/signatures/${encodeURIComponent(id)}`),
  sendSignatureFiles: (id: string) =>
    request<S["SentOut"]>("POST", `/api/v1/moderation/signatures/${encodeURIComponent(id)}/send-files`),
  reviewSignature: (id: string, approve: boolean, reason?: string) =>
    request<SignatureCase>("POST", `/api/v1/moderation/signatures/${encodeURIComponent(id)}/review`, {
      approve,
      reason: reason ?? null,
    }),

  // Оренди (M3)
  leases: () => request<LeaseSummary[]>("GET", "/api/v1/leases"),
  lease: (id: string) => request<Lease>("GET", l(id)),
  createLease: (body: LeaseCreate) => request<S["LeaseCreatedOut"]>("POST", "/api/v1/leases", body),
  updateConditions: (id: string, body: LeaseConditions) => request<Lease>("PUT", `${l(id)}/conditions`, body),
  acceptTerms: (id: string) => request<Lease>("POST", `${l(id)}/accept-terms`),
  requestChanges: (id: string, text: string) => request<Lease>("POST", `${l(id)}/request-changes`, { text }),
  leaveLease: (id: string) => request<void>("POST", `${l(id)}/leave`),
  cancelLease: (id: string) => request<Lease>("POST", `${l(id)}/cancel`),
  newInvite: (id: string, role: Invite["role"]) => request<Invite>("POST", `${l(id)}/invites`, { role }),
  previewInvite: (token: string) => request<InvitePreview>("GET", `/api/v1/invites/${encodeURIComponent(token)}`),
  acceptInvite: (token: string) => request<Lease>("POST", `/api/v1/invites/${encodeURIComponent(token)}/accept`),
  messages: (id: string, afterId = 0) => request<MessagesPage>("GET", `${l(id)}/messages?after_id=${afterId}`),
  postMessage: (id: string, body: string) => request<Message>("POST", `${l(id)}/messages`, { body }),
  markRead: (id: string, messageId: number) => request<void>("POST", `${l(id)}/read`, { message_id: messageId }),
  notifyPrefs: (id: string) => request<NotifyPrefs>("GET", `${l(id)}/notifications`),
  setNotifyPrefs: (id: string, body: NotifyPrefs) => request<NotifyPrefs>("PUT", `${l(id)}/notifications`, body),

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
