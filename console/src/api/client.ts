import axios from 'axios';

declare global {
  interface Window {
    /** 实例挂载前缀（AGENTCORE_BASE_PATH），由后端运行时注入；根路径部署时为空串 */
    __AGENTCORE_BASE_PATH__?: string;
  }
}

/**
 * 运行时挂载前缀，如 ``/instance01``；根路径部署返回 ``''``。
 * 值由后端在吐出 index.html 时替换占位符注入，同一镜像可部署在任意子路径下。
 */
export const getBasePath = (): string =>
  (window.__AGENTCORE_BASE_PATH__ ?? '').replace(/\/+$/, '');

/* ───────── Auth token storage (single-user opt-in auth) ───────── */

const TOKEN_KEY = 'agentcore.authToken';

export const getAuthToken = (): string | null => {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
};

export const setAuthToken = (token: string): void => {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch { /* ignore */ }
};

export const clearAuthToken = (): void => {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch { /* ignore */ }
};

/** Bearer header for raw ``fetch`` calls (SSE streams bypass axios). */
export const authHeaders = (): Record<string, string> => {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

/** Redirect to the login page (base-path aware, avoids /login loops). */
export const redirectToLogin = (): void => {
  const loginPath = `${getBasePath()}/login`;
  if (!window.location.pathname.startsWith(loginPath)) {
    window.location.href = loginPath;
  }
};

export const apiClient = axios.create({
  baseURL: `${getBasePath()}/api`,
  timeout: 30000,
});

// Attach the bearer token to every request when one is stored.
apiClient.interceptors.request.use((config) => {
  const token = getAuthToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Global 401 handling: drop the stale token and go to the login page.
// The auth endpoints themselves are public — a 401 there means bad
// credentials and is rendered inline by the login form.
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    const url: string = error?.config?.url ?? '';
    const isAuthEndpoint = url.startsWith('/auth/');
    if (status === 401 && !isAuthEndpoint) {
      clearAuthToken();
      redirectToLogin();
    }
    return Promise.reject(error);
  },
);
