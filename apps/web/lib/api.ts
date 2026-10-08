"use client";

import type { ApiErrorBody } from "@/lib/types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public retryAfter?: number,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(`/api/v1/${path.replace(/^\//, "")}`, {
    ...rest,
    method: rest.method ?? (json !== undefined ? "POST" : "GET"),
    headers: { ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...(rest.headers ?? {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
    cache: "no-store",
  });
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

export async function toError(res: Response): Promise<ApiError> {
  let body: Partial<ApiErrorBody> | null = null;
  try {
    body = (await res.json()) as ApiErrorBody;
  } catch {
    /* not json */
  }
  const ra = Number(res.headers.get("retry-after") ?? body?.error?.retry_after ?? NaN);
  return new ApiError(
    res.status,
    body?.error?.code ?? (res.status === 401 ? "unauthorized" : "internal"),
    body?.error?.message ?? `Request failed (${res.status}).`,
    Number.isFinite(ra) ? ra : undefined,
  );
}
