# AgentCore Console - Google 设计规范重构说明

## 变更概要

本次重构将 AgentCore Console 的现有 Ant Design 界面按照 Google 设计系统（Design Library: Google）进行了视觉升级，覆盖颜色、字体、圆角、阴影、布局与组件层级。后续建议中的三项已全部实现：深色模式、页面级统一组件、图表配色方案。

## 核心设计原则

- **扁平优先**：不使用投影表达层级，改用细边框与色块区分。
- **主色点睛**：主操作与当前态使用 `#4285f4`，其余区域保持克制。
- **信息密集**：紧凑间距（基准 3.84px），适合仪表盘式后台。
- **字体清晰**：UI 文案使用 DM Sans，代码/指标使用 JetBrains Mono。

## 改动文件

| 文件 | 说明 |
|---|---|
| `src/styles/google-tokens.css` | 新增：完整的 Google 设计 Token（颜色、字体、字号、间距、圆角、阴影），含 `.dark` 深色变量。 |
| `src/index.css` | 引入 Google Fonts 与 Token，设置全局字体、滚动条、焦点环。 |
| `src/App.tsx` | 通过 `ConfigProvider` 将 Ant Design 主题覆盖为 Google Token，并根据当前主题切换算法。 |
| `src/layouts/MainLayout.tsx` | 侧边栏改为浅蓝底（`#f0f6ff`）+ 白色主内容区 + 细边框卡片容器；新增主题切换按钮。 |
| `src/stores/appStore.ts` | 新增主题状态（light/dark/auto），持久化到 localStorage，自动切换 `html.dark`。 |
| `src/components/GoogleCard/index.tsx` | 新增：使用 Google Token 的卡片包装组件，自适应深浅模式。 |
| `src/components/GooglePageHeader/index.tsx` | 新增：统一页面标题组件。 |
| `src/styles/chart-colors.ts` | 新增：Google 图表配色（浅色/深色两套）。 |
| `src/pages/TokenUsage/TokenUsagePage.tsx` | 示例迁移：使用 GoogleCard、GooglePageHeader 与图表配色。 |
| `src/pages/AgentConfig/AgentConfigPage.tsx` | 运行配置页面美化。 |
| `src/pages/Chat/ChatPage.tsx` | 聊天页面美化。 |
| `src/pages/Inbox/InboxPage.tsx` | 收件箱页面美化。 |
| `src/pages/Channels/ChannelsPage.tsx` | 渠道页面美化。 |
| `src/pages/Sessions/SessionsPage.tsx` | 会话页面美化。 |
| `src/pages/CronJobs/CronJobsPage.tsx` | 定时任务页面美化。 |
| `src/pages/Heartbeat/HeartbeatPage.tsx` | 心跳页面美化。 |
| `src/pages/Files/FilesPage.tsx` | 文件页面美化。 |
| `src/pages/Skills/SkillsPage.tsx` | 技能页面美化。 |
| `src/pages/Tools/ToolsPage.tsx` | 工具页面美化。 |
| `src/pages/Mcp/McpPage.tsx` | MCP 页面美化。 |
| `src/pages/Acp/AcpPage.tsx` | ACP 页面美化。 |
| `src/pages/AgentStats/AgentStatsPage.tsx` | Agent 统计页面美化。 |
| `src/pages/Agents/AgentsPage.tsx` | Agent 列表页面美化。 |
| `src/pages/Models/ModelsPage.tsx` | 模型页面美化。 |
| `src/pages/SkillPool/SkillPoolPage.tsx` | 技能池页面美化。 |
| `src/pages/Environments/EnvironmentsPage.tsx` | 环境变量页面美化。 |
| `src/pages/Security/SecurityPage.tsx` | 安全页面美化。 |
| `src/pages/Backups/BackupsPage.tsx` | 备份页面美化。 |
| `src/pages/Voice/VoicePage.tsx` | 语音页面美化。 |
| `src/pages/Debug/DebugPage.tsx` | 调试页面美化。 |
| `docs/google-ui-refactor.md` | 本说明文档。 |
| `docs/google-preview.html` | 静态视觉预览。 |
| `docs/ui-migration-guide.md` | UI 迁移指南。 |

## Token 速查

### 颜色

| Token | 浅色模式 | 深色模式 |
|---|---|---|
| Primary | `#4285f4` | `#fc2c50` |
| Background | `#ffffff` | `#161616` |
| Card | `#ffffff` | `#1d1d1c` |
| Sidebar | `#f0f6ff` | `#171717` |
| Border | `#ebebeb` | `#1d1d1c` |
| Muted text | `#7f8d9f` | `#949494` |

### 字体

- 正文字体：`"DM Sans", ui-sans-serif, system-ui, sans-serif`
- 等宽字体：`"JetBrains Mono", ui-monospace, monospace`

### 圆角与间距

- 基础圆角：`8px`
- 基础间距单位：`0.24rem`（约 3.84px）

## 已实现的后续建议

### 1. 深色模式

- 在 `google-tokens.css` 中已定义 `.dark` 变量。
- `appStore` 提供 `themeMode`（light/dark/auto）与 `toggleTheme()`，状态持久化到 localStorage。
- `App.tsx` 监听 `resolvedTheme`，为 `ConfigProvider` 切换 `theme.darkAlgorithm` / `theme.defaultAlgorithm`。
- `MainLayout` 顶部右侧新增太阳/月亮切换按钮。
- 使用 `GoogleCard` 等组件的页面会自动跟随 CSS 变量切换深浅。

### 2. 页面级组件

- `GoogleCard`：替代原生 `Card`，统一边框、圆角、背景色，支持 `surface="muted"`。
- `GooglePageHeader`：统一页面标题、副标题与右上角操作区。
- `TokenUsagePage` 已作为示例迁移到这两个组件，其他页面可参照同样方式逐步替换。

### 3. 图表配色

- `src/styles/chart-colors.ts` 导出：
  - `chartColorsLight`：浅色模式 palette。
  - `chartColorsDark`：深色模式 palette。
  - `getChartColors(isDark?)`：根据当前主题返回对应数组。
  - `chartColorNames`：命名语义色。
- `TokenUsagePage` 的柱状图与统计高亮已改用该配色，会自动随主题变化。

## Ant Design 主题覆盖点

- `colorPrimary`: `#4285f4`
- `borderRadius`: `8`
- `fontFamily`: DM Sans
- `Menu` 组件：浅蓝底色、悬停 `#dbeafe`、选中 `#4285f4` 白字
- `Button` 组件：去除阴影，扁平边框
- `Card` / `Table` / `Input` / `Select`：统一 8px 圆角与 Google 边框色

## 使用示例

```tsx
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';
import { getChartColors } from '../../styles/chart-colors';
import { useAppStore } from '../../stores/appStore';

function SomePage() {
  const resolvedTheme = useAppStore((s) => s.resolvedTheme);
  const colors = getChartColors(resolvedTheme === 'dark');

  return (
    <div>
      <GooglePageHeader title="Overview" subtitle="Summary" />
      <GoogleCard title="Metrics">
        <div style={{ color: colors[0] }}>Primary metric</div>
      </GoogleCard>
    </div>
  );
}
```
