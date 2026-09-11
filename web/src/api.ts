export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: "include",
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      // Keep the HTTP fallback when the response is not JSON.
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export const api = {
  me: () => request<{ username: string }>("/auth/me"),
  login: (username: string, password: string) =>
    request<{ username: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  jobs: () => request<{ items: Job[]; total: number }>("/jobs"),
  job: (id: string) => request<Job>(`/jobs/${id}`),
  items: (id: string, params: string) =>
    request<{ items: Item[]; total: number; page: number; page_size: number }>(
      `/jobs/${id}/items${params ? `?${params}` : ""}`,
    ),
  createJob: (body: CreateJob) =>
    request<Job>("/jobs", { method: "POST", body: JSON.stringify(body) }),
  importJob: (file: File, jobName: string, accountId?: string, accountIds: string[] = []) => {
    const form = new FormData();
    form.append("file", file);
    if (jobName) form.append("job_name", jobName);
    if (accountIds.length > 1) form.append("telegram_account_ids", accountIds.join(","));
    else if (accountId || accountIds[0]) form.append("telegram_account_id", accountId || accountIds[0]);
    return request<Job>("/jobs/import", { method: "POST", body: form });
  },
  action: (id: string, action: "start" | "pause" | "resume" | "cancel") =>
    request<{ ok: boolean }>(`/jobs/${id}/${action}`, { method: "POST" }),
  deleteJob: (id: string) => request<{ ok: boolean }>(`/jobs/${id}`, { method: "DELETE" }),
  account: () => request<AccountStatus>("/account/status"),
  accounts: () => request<{ items: TelegramAccount[]; total: number }>("/account/accounts"),
  addAccount: (body: { phone: string; label?: string }) => request<{ id: string; ok: boolean }>("/account/accounts", { method: "POST", body: JSON.stringify(body) }),
  importTelegramSession: (file: File, label?: string, accountId?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (label) form.append("label", label);
    if (accountId) form.append("account_id", accountId);
    return request<{ ok: boolean; account: TelegramAccount }>("/account/session/import", { method: "POST", body: form });
  },
  startAccountLogin: (id: string) => request<LoginProgress>(`/account/accounts/${id}/login/start`, { method: "POST" }),
  migrateLegacySession: (id: string) => request<TelegramAccount>(`/account/accounts/${id}/session/migrate`, { method: "POST" }),
  startAccountSqlLogin: (id: string) => request<LoginProgress>(`/account/accounts/${id}/login/sql/start`, { method: "POST" }),
  setDefaultAccount: (id: string) => request<{ ok: boolean }>(`/account/accounts/${id}/default`, { method: "POST" }),
  logoutAccount: (id: string) => request<{ deleted: boolean }>(`/account/accounts/${id}/logout`, { method: "POST" }),
  deleteAccount: (id: string) => request<{ ok: boolean }>(`/account/accounts/${id}`, { method: "DELETE" }),
  startLogin: () => request<LoginProgress>("/account/login/start", { method: "POST" }),
  submitLogin: (body: { session_id: string; code?: string; password?: string }) =>
    request<LoginProgress>("/account/login/code", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelLogin: (session_id: string) =>
    request<{ ok: boolean }>("/account/login/cancel", {
      method: "POST",
      body: JSON.stringify({ session_id }),
    }),
  accountLogout: () => request<{ deleted: boolean }>("/account/logout", { method: "POST" }),
  startQrLogin: () => request<QrLoginState>("/account/qr/start", { method: "POST" }),
  qrLoginStatus: (qrId: string) => request<QrLoginState>(`/account/qr/${qrId}`),
  refreshQrLogin: (qrId: string) => request<QrLoginState>(`/account/qr/${qrId}/refresh`, { method: "POST" }),
  submitQr2fa: (qrId: string, password: string) => request<TelegramAccount>(`/account/qr/${qrId}/2fa`, { method: "POST", body: JSON.stringify({ password }) }),
  cancelQrLogin: (qrId: string) => request<{ ok: boolean }>(`/account/qr/${qrId}/cancel`, { method: "POST" }),
  managerProfile: (id: string) => request<ManagerProfile>(`/manager/profile/${id}`),
  updateManagerProfile: (id: string, body: { first_name: string; last_name?: string; about?: string }) =>
    request<ManagerProfile>(`/manager/profile/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  updateManagerUsername: (id: string, username: string) =>
    request<{ username: string }>(`/manager/profile/${id}/username`, { method: "PUT", body: JSON.stringify({ username }) }),
  managerSessions: (id: string) => request<{ items: ManagerSession[] }>(`/manager/security/${id}/sessions`),
  terminateManagerSession: (id: string, hash: string) => request<{ ok: boolean }>(`/manager/security/${id}/sessions/${hash}`, { method: "DELETE" }),
  managerSecurityMessages: (id: string) => request<{ items: ManagerSecurityMessage[] }>(`/manager/security/${id}/messages`),
  managerGroups: (id: string) => request<{ items: ManagerDialog[] }>(`/manager/groups/${id}`),
  managerJoin: (id: string, target: string) => request<ManagerDialog>(`/manager/groups/${id}/join`, { method: "POST", body: JSON.stringify({ target }) }),
  managerLeave: (id: string, target: string) => request<{ ok: boolean }>(`/manager/groups/${id}/leave`, { method: "POST", body: JSON.stringify({ target }) }),
  managerSend: (id: string, target: string, text: string) => request<{ id: number | null; date: string | null }>(`/manager/messages/${id}/send`, { method: "POST", body: JSON.stringify({ target, text }) }),
  managerCheckUsername: (id: string, username: string) => request<{ available: boolean; reason?: string | null }>(`/manager/profile/${id}/username-check?username=${encodeURIComponent(username)}`),
  managerUploadPhoto: (id: string, file: File) => { const form = new FormData(); form.append("file", file); return request<{ ok: boolean }>(`/manager/profile/${id}/photo`, { method: "POST", body: form }); },
  managerPhotoUrl: (id: string, version = 0) => `/api/manager/profile/${id}/photo?v=${version}`,
  terminateOtherManagerSessions: (id: string) => request<{ ok: boolean; terminated: number; failed: number }>(`/manager/security/${id}/sessions/terminate-others`, { method: "POST" }),
  managerOpenChat: (id: string, input: string, limit = 40) => request<ManagerChatOpen>(`/manager/messages/${id}/open`, { method: "POST", body: JSON.stringify({ input, limit }) }),
  managerChatHistory: (id: string, peer: string, limit = 40) => request<{ messages: ManagerChatMessage[] }>(`/manager/messages/${id}/history?peer=${encodeURIComponent(peer)}&limit=${limit}`),
  managerChatSend: (id: string, peer: string, text: string) => request<{ ok: boolean; message: ManagerChatMessage }>(`/manager/messages/${id}/chat-send`, { method: "POST", body: JSON.stringify({ peer, text }) }),
  managerTargetCheck: (target: string, account_ids: string[] = []) => request<ManagerTargetCheck>("/manager/target-check", { method: "POST", body: JSON.stringify({ target, account_ids }) }),
  managerReactions: (id: string) => request<{ items: string[] }>(`/manager/posts/${id}/reactions`),
  managerReact: (id: string, post_link: string, emoji: string, custom_emoji_id?: number) => request<{ ok: boolean; message_id: number }>(`/manager/posts/${id}/react`, { method: "POST", body: JSON.stringify({ post_link, emoji, custom_emoji_id }) }),
  managerView: (id: string, post_link: string) => request<{ ok: boolean; message_id: number; views?: number | null }>(`/manager/posts/${id}/view`, { method: "POST", body: JSON.stringify({ post_link }) }),
  managerBulkJoin: (account_ids: string[], target: string) => request<ManagerBulkResult>("/manager/bulk/join", { method: "POST", body: JSON.stringify({ account_ids, target }) }),
  managerBulkLeave: (account_ids: string[], target: string) => request<ManagerBulkResult>("/manager/bulk/leave", { method: "POST", body: JSON.stringify({ account_ids, target }) }),
  managerBulkTerminateOthers: (account_ids: string[]) => request<ManagerBulkResult>("/manager/bulk/terminate-others", { method: "POST", body: JSON.stringify({ account_ids }) }),
  managerAudit: () => request<{ items: ManagerAudit[] }>("/manager/audit"),
  uiPreferences: () => request<{ theme: "dark" | "light" }>("/config/ui"),
  setUiPreferences: (theme: "dark" | "light") => request<{ theme: "dark" | "light" }>("/config/ui", { method: "PUT", body: JSON.stringify({ theme }) }),
  config: () => request<RuntimeConfig>("/config"),
};

export type Job = {
  job_id: string;
  name: string | null;
  status: string;
  total: number;
  processed: number;
  found: number;
  not_discoverable: number;
  retry_queue: number;
  errors: number;
  pending: number;
  active: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  last_error_type: string | null;
  last_error_message: string | null;
  next_retry_at: string | null;
  account_blocked_until: string | null;
  telegram_account_phone?: string | null;
  job_mode?: "SINGLE" | "MULTI" | "BRANCH";
  branches?: Job[];
};

export type Item = {
  id: number;
  masked_phone: string;
  status: string;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  telegram_user_id: number | null;
  last_error_message: string | null;
  attempt_count: number;
  telegram_account_phone?: string | null;
};

export type CreateJob = {
  phone_numbers?: string;
  job_name?: string;
  max_attempts?: number;
  telegram_account_id?: string;
  mode?: "SINGLE" | "MULTI";
  account_batches?: { telegram_account_id: string; phone_numbers: string }[];
  auto_start?: boolean;
};

export type QrLoginState = {
  qr_id: string;
  url?: string;
  state: "WAIT_SCAN" | "WAIT_2FA" | "COMPLETED" | "EXPIRED" | "FAILED";
  expires_at?: string | null;
  error?: string | null;
  account?: TelegramAccount;
};

export type TelegramAccount = {
  id: string; label: string; phone: string; is_default: boolean; state: string;
  last_login_at?: string | null; login?: LoginProgress | null;
  session_backend?: "SQL" | "LEGACY_FILE" | "NONE";
  in_use?: boolean; blocked_until?: string | null; last_rate_limit_at?: string | null;
};

export type ManagerProfile = {
  id: number | null; phone?: string | null; first_name: string; last_name: string; username: string; about: string; premium?: boolean; verified?: boolean;
};

export type ManagerSession = {
  hash: string; current: boolean; device_model: string; platform: string; system_version: string; app_name: string; app_version: string; ip: string; country: string; region: string; date_active: string | null;
};

export type ManagerSecurityMessage = { id: number | null; text: string; date: string | null; };

export type ManagerDialog = { id: string; title: string; username: string; is_group: boolean; is_channel: boolean; unread_count: number; };

export type ManagerBulkResult = {
  total: number; succeeded: number; failed: number;
  results: { account_id: string; status: "ok" | "failed"; detail?: string; result?: unknown }[];
};

export type AccountStatus = {
  state: string;
  phone?: string | null;
  login?: { session_id: string; phone: string; state: string; error?: string | null };
  error?: string;
};

export type LoginProgress = {
  session_id: string;
  state: string;
  phone?: string;
  error?: string | null;
};

export type RuntimeConfig = {
  api_id_masked: string;
  api_hash_set: boolean;
  api_phone_number: string | null;
  proxy_set: boolean;
  database_path: string;
  database_backend: string;
  default_phone_region: string;
  max_attempts: number;
  base_retry_delay_seconds: number;
  max_retry_delay_seconds: number;
  min_request_interval_seconds: number | null;
  auto_resume: boolean;
  worker_lease_seconds: number;
  lease_takeover_grace_seconds: number;
  in_flight_recovery_grace_seconds: number;
  web_ui: { username: string; session_hours: number; telegram_account_owned: boolean };
};
export type ManagerPeer = { id: string; ref?: string; title: string; username?: string | null; kind?: string; is_bot?: boolean };
export type ManagerChatMessage = { id: number; out: boolean; text: string; media?: string | null; date?: string | null; service?: boolean; sender_id?: string | null };
export type ManagerChatOpen = { peer: ManagerPeer; started: boolean; start_param?: string | null; messages: ManagerChatMessage[] };
export type ManagerTargetRow = { id: string; label?: string; status: string; detail: string; peer?: ManagerPeer };
export type ManagerTargetCheck = { target: string; peer?: ManagerPeer | null; total: number; present: ManagerTargetRow[]; absent: ManagerTargetRow[]; skipped: ManagerTargetRow[]; failed: ManagerTargetRow[]; results: ManagerTargetRow[] };
export type ManagerAudit = { id: number; action: string; account_id?: string | null; status: string; detail?: string | null; created_at: string };
