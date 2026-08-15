import { create } from 'zustand';
import type { Locale, FlatMessages } from './types';
import { LOCALE_LABELS } from './types';

import zhCommon from './locales/zh-CN/common.json';
import zhMenu from './locales/zh-CN/menu.json';
import zhChat from './locales/zh-CN/chat.json';
import zhAgents from './locales/zh-CN/agents.json';
import zhFiles from './locales/zh-CN/files.json';
import zhModels from './locales/zh-CN/models.json';
import zhSessions from './locales/zh-CN/sessions.json';
import zhTools from './locales/zh-CN/tools.json';
import zhMcp from './locales/zh-CN/mcp.json';
import zhStats from './locales/zh-CN/stats.json';
import zhSkillPool from './locales/zh-CN/skillPool.json';
import zhEnvs from './locales/zh-CN/envs.json';
import zhTokenUsage from './locales/zh-CN/tokenUsage.json';
import zhAgentSelector from './locales/zh-CN/agentSelector.json';
import zhAgentConfig from './locales/zh-CN/agentConfig.json';
import zhSecurity from './locales/zh-CN/security.json';

import enCommon from './locales/en/common.json';
import enMenu from './locales/en/menu.json';
import enChat from './locales/en/chat.json';
import enAgents from './locales/en/agents.json';
import enFiles from './locales/en/files.json';
import enModels from './locales/en/models.json';
import enSessions from './locales/en/sessions.json';
import enTools from './locales/en/tools.json';
import enMcp from './locales/en/mcp.json';
import enStats from './locales/en/stats.json';
import enSkillPool from './locales/en/skillPool.json';
import enEnvs from './locales/en/envs.json';
import enTokenUsage from './locales/en/tokenUsage.json';
import enAgentSelector from './locales/en/agentSelector.json';
import enAgentConfig from './locales/en/agentConfig.json';
import enSecurity from './locales/en/security.json';

const STORAGE_KEY = 'agentcore.locale';
const FALLBACK_LOCALE: Locale = 'zh-CN';

/** All locale bundles keyed by locale code. */
const bundles: Record<Locale, Record<string, Record<string, string>>> = {
  'zh-CN': {
    common: zhCommon,
    menu: zhMenu,
    chat: zhChat,
    agents: zhAgents,
    files: zhFiles,
    models: zhModels,
    sessions: zhSessions,
    tools: zhTools,
    mcp: zhMcp,
    stats: zhStats,
    skillPool: zhSkillPool,
    envs: zhEnvs,
    tokenUsage: zhTokenUsage,
    agentSelector: zhAgentSelector,
    agentConfig: zhAgentConfig,
    security: zhSecurity,
  },
  en: {
    common: enCommon,
    menu: enMenu,
    chat: enChat,
    agents: enAgents,
    files: enFiles,
    models: enModels,
    sessions: enSessions,
    tools: enTools,
    mcp: enMcp,
    stats: enStats,
    skillPool: enSkillPool,
    envs: enEnvs,
    tokenUsage: enTokenUsage,
    agentSelector: enAgentSelector,
    agentConfig: enAgentConfig,
    security: enSecurity,
  },
};

/** Flatten nested module map into dot-separated keys. */
function flatten(modules: Record<string, Record<string, string>>): FlatMessages {
  const result: FlatMessages = {};
  for (const [mod, entries] of Object.entries(modules)) {
    for (const [key, value] of Object.entries(entries)) {
      result[`${mod}.${key}`] = value;
    }
  }
  return result;
}

/** Pre-flatten all locales. */
const flatBundles: Record<Locale, FlatMessages> = {
  'zh-CN': flatten(bundles['zh-CN']),
  en: flatten(bundles.en),
};

/** Detect initial locale from localStorage or browser. */
function detectInitialLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && stored in LOCALE_LABELS) return stored as Locale;
  } catch { /* ignore */ }

  const nav = navigator.language;
  if (nav.startsWith('zh')) return 'zh-CN';
  return 'en';
}

interface I18nState {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
}

export const useI18nStore = create<I18nState>((set, get) => ({
  locale: detectInitialLocale(),

  setLocale: (locale: Locale) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, locale);
    } catch { /* ignore */ }
    set({ locale });
  },

  t: (key: string, params?: Record<string, string | number>) => {
    const { locale } = get();
    const messages = flatBundles[locale];
    let text = messages[key];

    // Fallback to zh-CN if key not found in current locale
    if (text === undefined && locale !== FALLBACK_LOCALE) {
      text = flatBundles[FALLBACK_LOCALE][key];
    }

    // If still not found, return the key itself
    if (text === undefined) return key;

    // Interpolate {{param}} placeholders
    if (params) {
      return text.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
        params[name] !== undefined ? String(params[name]) : `{{${name}}}`,
      );
    }
    return text;
  },
}));

/** Convenience hook for components. */
export const useI18n = () => {
  const locale = useI18nStore((s) => s.locale);
  const setLocale = useI18nStore((s) => s.setLocale);
  const t = useI18nStore((s) => s.t);
  return { locale, setLocale, t };
};

export { FALLBACK_LOCALE, STORAGE_KEY };
