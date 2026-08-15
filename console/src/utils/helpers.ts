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
