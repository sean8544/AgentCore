import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { getBasePath } from '../api/client';

type ThemeMode = 'light' | 'dark' | 'auto';

interface AppState {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  themeMode: ThemeMode;
  resolvedTheme: 'light' | 'dark';
  setThemeMode: (mode: ThemeMode) => void;
  toggleTheme: () => void;
}

const resolveTheme = (mode: ThemeMode): 'light' | 'dark' => {
  if (mode === 'auto') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  return mode;
};

/** 同域多实例（K8s 子路径部署）时按挂载前缀隔离存储，根路径部署保持原 key。 */
const storageSuffix = getBasePath();

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      sidebarCollapsed: false,
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      themeMode: 'light',
      resolvedTheme: 'light',
      setThemeMode: (mode) => {
        const resolved = resolveTheme(mode);
        set({ themeMode: mode, resolvedTheme: resolved });
        document.documentElement.classList.toggle('dark', resolved === 'dark');
      },
      toggleTheme: () => {
        const next = get().resolvedTheme === 'light' ? 'dark' : 'light';
        set({ themeMode: next, resolvedTheme: next });
        document.documentElement.classList.toggle('dark', next === 'dark');
      },
    }),
    {
      name: storageSuffix ? `agentcore-app-storage-${storageSuffix}` : 'agentcore-app-storage',
      partialize: (state) => ({ sidebarCollapsed: state.sidebarCollapsed, themeMode: state.themeMode }),
      onRehydrateStorage: () => (state) => {
        if (state) {
          const resolved = resolveTheme(state.themeMode);
          state.resolvedTheme = resolved;
          document.documentElement.classList.toggle('dark', resolved === 'dark');
        }
      },
    },
  ),
);
