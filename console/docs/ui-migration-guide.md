# UI 页面美化迁移指南

本指南用于将各功能页面统一迁移到 Google 设计系统。

## 必须遵循的改动

### 1. 导入新组件

在每个页面顶部添加：

```tsx
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';
```

如果页面原本使用了 `Card`，可从 `antd` 中移除 `Card` 导入。

### 2. 替换页面标题

将原本类似：

```tsx
<div style={{ padding: 24 }}>
  <Card>
    <Space align="center" style={{ marginBottom: 4 }}>
      <SomeIcon style={{ fontSize: 20, color: '#1677ff' }} />
      <Title level={3} style={{ margin: 0 }}>{t('page.title')}</Title>
    </Space>
    <Text type="secondary">{t('page.subtitle')}</Text>
    <Divider style={{ margin: '16px 0' }} />
```

替换为：

```tsx
<div>
  <GooglePageHeader
    icon={<SomeIcon />}
    title={t('page.title')}
    subtitle={t('page.subtitle')}
    extra={/* 可选的右上角控件，如 Select / Button */}
  />
```

注意：外层 `padding: 24` 通常要去掉，因为 `MainLayout` 的 `Content` 已经有内边距。

### 3. 替换 Card

将所有 `Card` 替换为 `GoogleCard`：

```tsx
<GoogleCard title={t('...')}>
  ...
</GoogleCard>
```

如果原 `Card` 有 `bodyStyle` / `headStyle` / `style`，保留并合并。

### 4. 颜色 Token 替换

不要使用硬编码颜色，改用 CSS 变量：

| 原颜色 | 替换为 |
|---|---|
| `#1677ff` (Ant Design 蓝) | `var(--google-primary)` |
| `#999` / `#888` | `var(--google-muted-foreground)` |
| `#555` / `#666` | `var(--google-foreground)` |
| `#722ed1` (紫色) | `var(--google-chart-2)` 或 `var(--google-primary)` |
| `orange` Tag | 保留 Tag color，但优先使用语义色 |
| `#52c41a` / 成功绿 | `var(--google-chart-5)` 或 `#34a853` |

### 5. 间距 Token 替换

优先使用 Google spacing token：

- 小间距：`var(--google-space-3)` 或 `var(--google-space-4)`
- 中等间距：`var(--google-space-6)` 或 `var(--google-space-8)`
- 区块间距：`var(--google-space-8)` 或 `var(--google-space-10)`

### 6. 圆角 Token 替换

- 卡片/面板圆角：`var(--google-radius-lg)`
- 按钮/输入框圆角：`var(--google-radius-md)`
- 小标签圆角：`var(--google-radius-sm)`

### 7. 布局优化

- 多个设置项/统计卡片优先使用 CSS Grid：`gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))'`
- 列表项可改为带边框的卡片式条目（参考 AgentConfigPage 拓扑列表）
- 避免过长的单行布局，使用响应式换行

### 8. 字体

标题字号已内置于 `GooglePageHeader`。正文保持默认 `var(--google-font-sans)`。

### 9. 删除未使用导入

迁移后请删除未使用的导入，如 `Title`、`Card`、`Divider` 等。

## 示例：页面骨架

```tsx
export default function SomePage() {
  const { t } = useI18n();

  return (
    <div>
      <GooglePageHeader
        icon={<ToolOutlined />}
        title={t('page.title')}
        subtitle={t('page.subtitle')}
      />

      <GoogleCard title={t('page.section1')}>
        ...
      </GoogleCard>

      <GoogleCard title={t('page.section2')} style={{ marginTop: 'var(--google-space-8)' }}>
        ...
      </GoogleCard>
    </div>
  );
}
```

## 验证

每个页面修改后，在 `console` 目录运行：

```bash
npx tsc --noEmit
```

确保没有类型错误。
