import type { components } from "./schema";

type S = components["schemas"];
export type Me = S["MeOut"];
export type User = S["UserOut"];
export type Mode = S["AppMode"];
export type TenantProfile = S["TenantProfile"];
export type Personal = S["PersonalOut"];
export type PersonalIn = S["PersonalIn"];
export type AddressCandidate = S["AddressCandidate"];
export type City = S["CityOut"];
export type PropertyCreate = S["PropertyCreate"];
export type PropertyUpdate = S["PropertyUpdate"];
export type PropertyDetails = S["PropertyDetails-Input"];
export type Property = S["PropertyOut"];
export type Unit = S["UnitOut"];
export type Photo = S["PhotoOut"];
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
export type PaymentsOverview = S["PaymentsOverview"];
export type Charge = S["ChargeOut"];
export type PaymentItem = S["PaymentOut"];
export type PayInfo = S["PayInfo"];

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

/** Файл квитанції для перегляду (PDF або фото). */
async function requestFile(path: string): Promise<{ data: ArrayBuffer; mime: string }> {
  if (!apiConfigured()) throw new ApiFailure("no_api");
  let response: Response;
  try {
    response = await fetch(API_URL + path, {
      headers: { Authorization: `tma ${initData}`, "ngrok-skip-browser-warning": "1" },
    });
  } catch {
    throw new ApiFailure("network");
  }
  if (response.status === 401) throw new ApiFailure("unauthorized");
  if (!response.ok) throw new ApiFailure(response.status === 404 ? "domain" : "server", "not_found");
  return { data: await response.arrayBuffer(), mime: response.headers.get("Content-Type") ?? "" };
}

/** PDF для перегляду в застосунку (договір або чернетка). */
async function requestPdf(path: string): Promise<{ data: ArrayBuffer; draft: boolean }> {
  if (!apiConfigured()) throw new ApiFailure("no_api");
  let response: Response;
  try {
    response = await fetch(API_URL + path, {
      headers: { Authorization: `tma ${initData}`, "ngrok-skip-browser-warning": "1" },
    });
  } catch {
    throw new ApiFailure("network");
  }
  if (response.status === 401) throw new ApiFailure("unauthorized");
  if (!response.ok) {
    const err = (await response.json().catch(() => null)) as { error?: string } | null;
    if (response.status === 503) throw new ApiFailure("unavailable", err?.error);
    if (err?.error) throw new ApiFailure("domain", err.error);
    throw new ApiFailure("server");
  }
  return { data: await response.arrayBuffer(), draft: response.headers.get("X-Contract-Draft") === "1" };
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
  personal: () => request<Personal>("GET", "/api/v1/me/personal"),
  savePersonal: (body: PersonalIn) => request<Personal>("PUT", "/api/v1/me/personal", body),
  uploadAvatar: (data: Blob) => {
    const form = new FormData();
    form.append("file", data, "avatar.jpg");
    return request<User>("PUT", "/api/v1/me/avatar", form);
  },
  removeAvatar: () => request<User>("DELETE", "/api/v1/me/avatar"),
  /** Аватар будь-кого: шлях з версією, щоб нове фото не бралось із кешу. */
  avatarFile: (userId: string, version: string) =>
    requestFile(`/api/v1/users/${encodeURIComponent(userId)}/avatar?v=${encodeURIComponent(version)}`),
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
  photos: (id: string) => request<Photo[]>("GET", `${p(id)}/photos`),
  addPhoto: (id: string, data: Blob) => {
    const form = new FormData();
    form.append("file", data, "photo");
    return request<Photo[]>("POST", `${p(id)}/photos`, form);
  },
  deletePhoto: (id: string, photoId: string) =>
    request<Photo[]>("DELETE", `${p(id)}/photos/${encodeURIComponent(photoId)}`),
  coverPhoto: (id: string, photoId: string) =>
    request<Photo[]>("POST", `${p(id)}/photos/${encodeURIComponent(photoId)}/cover`),
  photoFile: (path: string) => requestFile(path),

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

  // Оплати (M5)
  payments: (leaseId: string) => request<PaymentsOverview>("GET", `${l(leaseId)}/payments`),
  payInfo: (chargeId: string) => request<PayInfo>("GET", `/api/v1/charges/${encodeURIComponent(chargeId)}/pay-info`),
  reportCash: (chargeId: string, amount: string, note?: string) =>
    request<PaymentsOverview>("POST", `/api/v1/charges/${encodeURIComponent(chargeId)}/cash`, { amount, note: note ?? null }),
  reportReceipt: (chargeId: string, data: Blob, filename: string, amount: string) => {
    const form = new FormData();
    form.append("file", data, filename);
    form.append("amount", amount);
    return request<PaymentsOverview>("POST", `/api/v1/charges/${encodeURIComponent(chargeId)}/receipt`, form);
  },
  markReceived: (chargeId: string, amount: string) =>
    request<PaymentsOverview>("POST", `/api/v1/charges/${encodeURIComponent(chargeId)}/received`, { amount, note: null }),
  decidePayment: (paymentId: string, accept: boolean, reason?: string) =>
    request<PaymentsOverview>("POST", `/api/v1/payments/${encodeURIComponent(paymentId)}/decision`, {
      accept,
      reason: reason ?? null,
    }),
  paymentReceipt: (paymentId: string) => requestFile(`/api/v1/payments/${encodeURIComponent(paymentId)}/receipt`),
  sendReceipt: (paymentId: string) =>
    request<S["SentOut"]>("POST", `/api/v1/payments/${encodeURIComponent(paymentId)}/send`),

  // Договори (M4)
  contract: (leaseId: string) => request<ContractState>("GET", `${l(leaseId)}/contract`),
  setLegalName: (leaseId: string, full_name: string) =>
    request<ContractState>("PUT", `${l(leaseId)}/contract/name`, { full_name }),
  contractPreview: (leaseId: string) => requestPdf(`${l(leaseId)}/contract/preview`),
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
  leasePhotos: (id: string) => request<Photo[]>("GET", `${l(id)}/photos`),
  createLease: (body: LeaseCreate) => request<S["LeaseCreatedOut"]>("POST", "/api/v1/leases", body),
  updateConditions: (id: string, body: LeaseConditions) => request<Lease>("PUT", `${l(id)}/conditions`, body),
  acceptTerms: (id: string) => request<Lease>("POST", `${l(id)}/accept-terms`),
  requestChanges: (id: string, text: string) => request<Lease>("POST", `${l(id)}/request-changes`, { text }),
  leaveLease: (id: string) => request<void>("POST", `${l(id)}/leave`),
  cancelLease: (id: string) => request<Lease>("POST", `${l(id)}/cancel`),
  hideLease: (id: string) => request<void>("DELETE", l(id)),
  requestTermination: (id: string, date: string, reason: string) =>
    request<Lease>("POST", `${l(id)}/termination`, { date, reason }),
  withdrawTermination: (id: string) => request<Lease>("DELETE", `${l(id)}/termination`),
  proposeChanges: (id: string, body: S["ProposalIn"]) => request<Lease>("POST", `${l(id)}/proposal`, body),
  respondProposal: (id: string, accept: boolean) =>
    request<Lease>("POST", `${l(id)}/proposal/respond`, { accept }),
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
