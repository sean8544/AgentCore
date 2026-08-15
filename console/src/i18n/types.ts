/**
 * Supported locale identifiers.
 * To add a new language, add its code here and create a matching directory under locales/.
 */
export type Locale = 'zh-CN' | 'en';

export const LOCALE_LABELS: Record<Locale, string> = {
  'zh-CN': '中文',
  en: 'English',
};

/** Flat key-value map produced by flattening nested locale JSON files. */
export type FlatMessages = Record<string, string>;
