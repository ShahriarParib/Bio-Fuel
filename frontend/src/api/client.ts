/** Typed fetch wrapper. All types live in ./types.ts (mirror of 21_API_CONTRACTS.md). */
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

let operatorToken: string | null = null;
export const setToken = (t: string | null) => {
  operatorToken = t;
};

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (operatorToken) headers.Authorization = `Bearer ${operatorToken}`;
  const res = await fetch(`/api${path}`, { ...init, headers: { ...headers, ...(init.headers as object) } });
  if (!res.ok) {
    let code = "HTTP_" + res.status;
    let message = res.statusText;
    try {
      const body = await res.json();
      code = body?.detail?.code ?? body?.error?.code ?? code;
      message = body?.detail?.message ?? body?.error?.message ?? message;
    } catch {
      /* non-json */
    }
    throw new ApiError(res.status, code, message);
  }
  return (await res.json()) as T;
}
