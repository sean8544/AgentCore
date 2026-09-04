/**
 * Shared utility functions used across multiple pages.
 */

/**
 * Extract a human-readable error message from an unknown error object.
 * Handles Axios-style errors with `response.data.detail` as well as
 * standard Error instances.
 */
export function extractErrorMessage(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const resp = (error as { response?: { data?: { detail?: unknown } } }).response;
    const detail = resp?.data?.detail;
    if (typeof detail === 'string' && detail) return detail;
    if (detail != null) return JSON.stringify(detail);
  }
  if (error instanceof Error) return error.message;
  return '请求失败';
}

/**
 * Format a date-time string for display. Returns '—' for null/undefined,
 * or the original string if parsing fails.
 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

/**
 * Normalise a markdown link href into a workspace-relative file path when
 * it references a workspace file; returns null for external/anchor links.
 *
 * Handles the shapes an agent typically emits:
 * - `report.md`, `./tmp/report.md`            — relative paths
 * - `/tmp/report.md`, `C:\Users\x\report.md`  — absolute paths that the
 *   sandboxed backend mirrors inside the workspace root
 * - `/api/agents/{id}/files/download?path=…`  — download endpoint URLs
 */
export function toWorkspaceRelPath(href: string): string | null {
  let h = (href ?? '').trim();
  if (!h || h.startsWith('#')) return null;

  // Download endpoint URL → use its path query param.
  if (h.includes('files/download')) {
    const m = h.match(/[?&]path=([^&]+)/);
    if (m) h = m[1];
  } else if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(h)) {
    return null; // http(s), mailto, … — genuine external link
  }

  try {
    h = decodeURIComponent(h);
  } catch {
    /* keep raw */
  }
  h = h
    .replace(/\\/g, '/')
    .split(/[?#]/)[0]
    .replace(/^[A-Za-z]:/, '')
    .replace(/^\/+/, '')
    .replace(/^\.\//, '');
  if (!h || h === '.') return null;
  // Only intercept file-looking targets (last segment carries an extension).
  const last = h.split('/').pop() ?? '';
  if (!last.includes('.')) return null;
  return h;
}
