import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * 构建时 base 占位符 —— 后端吐出 index.html 时将其替换为实际挂载前缀
 * （见 api.py 的 BasePathMiddleware / _SpaStaticFiles），使同一镜像可部署
 * 在任意子路径下（如 K8s 的 /instance01、/instance02）。
 */
const BASE_PATH_PLACEHOLDER = '/__AGENTCORE_BASE_PATH__/'

/**
 * dev 模式下 Vite 自己吐 index.html（不经过后端替换），
 * 直接把 VITE_BASE_PATH 注入占位符。
 */
const injectBasePath = (basePath: string): Plugin => ({
  name: 'agentcore:inject-base-path',
  apply: 'serve',
  transformIndexHtml(html) {
    return html.split('__AGENTCORE_BASE_PATH__').join(basePath)
  },
})

/**
 * @a2ui/react v0_9 ships its CSS-module class maps as empty objects (the
 * package expects the *consumer* bundler to compile its *.module.css), so
 * catalog controls render with no class names and the catalog stylesheet
 * never matches.  Shim each empty map with a proxy that echoes the property
 * name, restoring the plain class names styled by src/styles/a2ui.css.
 */
const shimA2uiCssModules = (): Plugin => ({
  name: 'agentcore:shim-a2ui-css-modules',
  transform(code, id) {
    if (!id.includes('@a2ui/react') || !id.includes('v0_9')) return null
    if (!/var \w+_default = \{\};/.test(code)) return null
    const shim =
      'const __a2uiClassShim = new Proxy(Object.create(null), { get: (_t, k) => (typeof k === "string" ? k : undefined) });\n'
    return {
      code: shim + code.replace(/var (\w+_default) = \{\};/g, 'var $1 = __a2uiClassShim;'),
      map: null,
    }
  },
})

export default defineConfig(({ mode, command }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // API 代理目标可用环境变量 VITE_API_TARGET 覆盖（如指向非默认后端端口）
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8000'
  // 本地模拟子路径部署：VITE_BASE_PATH=/dev → http://localhost:3000/dev/
  const trimmed = env.VITE_BASE_PATH ? env.VITE_BASE_PATH.replace(/^\/+|\/+$/g, '') : ''
  const devBase = trimmed ? `/${trimmed}/` : '/'
  return {
    plugins: [react(), injectBasePath(trimmed ? `/${trimmed}` : ''), shimA2uiCssModules()],
    // 构建产物使用占位符 base，由后端运行时替换；dev 时直接用实际前缀
    base: command === 'build' ? BASE_PATH_PLACEHOLDER : devBase,
    server: {
      port: 3000,
      proxy: {
        [`${devBase}api`]: {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
