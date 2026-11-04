import type { components } from "./schema";

export type Me = components["schemas"]["MeOut"];

export type ApiError = "no_api" | "unauthorized" | "network" | "server";

export class ApiFailure extends Error {
  constructor(public readonly kind: ApiError) {
    super(kind);
  }
}

// Значення-заглушка (напр. TODO у змінних GitHub) = API ще не налаштовано.
const RAW_API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "";
const API_URL = /^https?:\/\/[^\s/]+/.test(RAW_API_URL) ? RAW_API_URL.replace(/\/$/, "") : "";

export function apiConfigured(): boolean {
  return API_URL !== "";
}

async function get<T>(path: string, initData: string): Promise<T> {
  if (!apiConfigured()) throw new ApiFailure("no_api");
  let response: Response;
  try {
    response = await fetch(API_URL + path, {
      headers: {
        Authorization: `tma ${initData}`,
        // Безкоштовний ngrok інакше віддає HTML-попередження замість відповіді API.
        "ngrok-skip-browser-warning": "1",
      },
    });
  } catch {
    throw new ApiFailure("network");
  }
  if (response.status === 401) throw new ApiFailure("unauthorized");
  if (!response.ok) throw new ApiFailure("server");
  return (await response.json()) as T;
}

export const api = {
  me: (initData: string) => get<Me>("/api/v1/me", initData),
};
