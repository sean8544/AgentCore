---
name: browser-use
description: "浏览器自动化技能：当用户需要打开网页、填写表单、点击按钮、截图、抓取网页内容、自动化操作网页时使用此技能。需配合 Playwright MCP 服务器使用。"
---

# 浏览器自动化技能

本技能指导 Agent 通过 Playwright MCP 工具进行浏览器自动化操作。

## 何时使用

- 用户要求打开某个网页或网站
- 需要填写网页表单、点击按钮、提交数据
- 需要网页截图或获取页面内容
- 需要自动化测试网页功能
- 需要抓取网页上的信息
- 用户说“帮我看看这个网页”“打开浏览器”“截个图”

## 前置条件

本技能依赖 Playwright MCP 服务器。若尚未配置，请在 MCP 管理页面添加：

- 服务器 ID：`playwright`
- 传输方式：`STDIO`
- 启动命令：`npx -y @playwright/mcp@latest`

## 核心工作流

浏览器操作遵循「快照 → 动作 → 确认」循环：

1. **导航** — 用 `browser_navigate` 打开目标 URL
2. **快照** — 用 `browser_snapshot` 获取页面可访问性树（核心！）
3. **动作** — 根据快照中的 ref 执行 `browser_click`、`browser_type` 等操作
4. **确认** — 操作后再次 `browser_snapshot` 确认结果

## 工具速查

| 工具 | 用途 | 关键参数 |
|------|------|----------|
| `browser_navigate` | 打开/跳转 URL | `url` |
| `browser_snapshot` | 获取可访问性树 | — |
| `browser_click` | 点击元素 | `element`（ref） |
| `browser_type` | 输入文本 | `element`（ref）、`text` |
| `browser_press_key` | 按键 | `key`（如 Enter、Tab） |
| `browser_select_option` | 下拉选择 | `element`（ref）、`values` |
| `browser_hover` | 悬停 | `element`（ref） |
| `browser_screenshot` | 截图 | `filename`（可选） |
| `browser_evaluate` | 执行 JavaScript | `expression` |
| `browser_tab_new` | 新标签页 | — |
| `browser_tab_list` | 列出标签页 | — |
| `browser_tab_select` | 切换标签页 | `index` |
| `browser_tab_close` | 关闭标签页 | `index` |
| `browser_wait` | 等待 | `time`（秒） |

## 最佳实践

### 优先使用快照而非截图

`browser_snapshot` 返回结构化的可访问性树，包含元素角色、名称和 ref：
- Token 消耗远低于截图
- 元素定位更精确（基于语义而非像素）
- 仅在需要视觉验证时使用 `browser_screenshot`

### 基于 ref 操作元素

快照输出中每个可交互元素都有一个 `ref`（如 `e42`），后续操作直接引用：

```
快照输出：
[button] "提交订单" [ref=e15]
[textbox] "搜索..." [ref=e16]

操作：
browser_click(element="e15")     # 点击“提交订单”
browser_type(element="e16", text="关键词")  # 在搜索框输入
```

### 处理动态内容

- 页面加载后等待 1-2 秒再快照：`browser_wait(time=2)`
- 点击按钮后等待页面更新再快照确认
- 下拉菜单、弹窗等需要先触发再快照

### 登录与验证码

- 遇到登录墙时，告知用户需要手动登录
- 建议用 headed 模式（需 MCP 配置 `--headed` 参数）让用户协助
- 验证码场景建议截图发给用户确认

### 常见场景模板

**场景 1：抓取页面文本**
```
browser_navigate(url="https://example.com")
browser_snapshot()  → 从可访问性树中提取文本
```

**场景 2：填写并提交表单**
```
browser_navigate(url="https://example.com/form")
browser_snapshot()  → 找到表单元素的 ref
browser_type(element="e5", text="张三")
browser_type(element="e6", text="zhangsan@email.com")
browser_click(element="e7")  → 点击提交
browser_snapshot()  → 确认提交成功
```

**场景 3：网页截图**
```
browser_navigate(url="https://example.com")
browser_screenshot(filename="screenshot.png")
```

## 注意事项

- 每次操作后都应 snapshot 确认结果，避免盲操作
- 避免在循环中频繁 snapshot，合理等待页面稳定
- 大页面快照可能较长，提醒用户耐心等待
- 文件下载场景需确认下载路径
- 敏感操作（如支付、删除）前务必截图让用户确认
