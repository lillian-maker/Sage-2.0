---
title: Sage App UI 开发设计交付
date: 2026-10-22
edition: 0.1
status: 设计交付与本地交互示例，待产品验收
scope: App 视觉、Web 组件与品牌资产
---

# Sage App UI 统一交付入口

同伴开发 App 时先读 [UI Kit 接入说明](../drafts/sage-app-ui-kit-20261022/README.md)与[完整设计规范](../drafts/sage-app-ui-kit-20261022/DESIGN_SPEC.md)。当前包位于 `drafts/sage-app-ui-kit-20261022/`，包含当前规范范围的全部组件。

| 内容 | 入口 |
| --- | --- |
| 整包下载 | [sage-app-ui-kit-20261022.zip](../drafts/sage-app-ui-kit-20261022.zip) |
| 开发说明和交互 API | [README.md](../drafts/sage-app-ui-kit-20261022/README.md) |
| 16 张页面图，亮色和纯黑各 8 张 | [双主题画廊](../drafts/sage-app-ui-kit-20261022/visuals/index.html) |
| 全部 PNG/SVG 文件 | [visuals/](../drafts/sage-app-ui-kit-20261022/visuals/) |
| 38 类组件与可复制 HTML | [index.html](../drafts/sage-app-ui-kit-20261022/index.html) |
| 38 个 HTML 局部片段 | [snippets/](../drafts/sage-app-ui-kit-20261022/snippets/) |
| 页面交互样例 | [app.html](../drafts/sage-app-ui-kit-20261022/app.html) |
| 配色、字号、尺寸、动效变量 | [tokens.json](../drafts/sage-app-ui-kit-20261022/tokens.json) |
| CSS 与原生 DOM 交互 | [components.css](../drafts/sage-app-ui-kit-20261022/components.css)、[components.js](../drafts/sage-app-ui-kit-20261022/components.js) |
| 32 个统一 SVG 图标 | [icons.svg](../drafts/sage-app-ui-kit-20261022/icons.svg) |
| 当前 53 人头像和映射 | [assets/avatars/](../drafts/sage-app-ui-kit-20261022/assets/avatars/)、[匿名尺寸预览](../drafts/sage-app-ui-kit-20261022/avatars.html) |
| 正式品牌 SVG | [assets/brand/](../drafts/sage-app-ui-kit-20261022/assets/brand/) |
| 校验结果与本地浏览器证据 | [checks/](../drafts/sage-app-ui-kit-20261022/checks/) |

GitHub 展示源码与图片；HTML 的实时交互请整包下载，在包根目录运行 `python3 -m http.server 8915 --bind 127.0.0.1`，然后打开 `http://127.0.0.1:8915/`。无需安装前端依赖。

## 设计基线

专家页按用户最新选择只保留四列，三列方案及列数切换已移除。浅蓝侧栏配白色内容；暗色主背景 `#000000`。主按钮文字/图标为 `#14243A`，对话按钮高 40px、胶囊形。职位优先，使命完整且不加粗；点击头像进入资料，卡片唯一主操作为“对话”。账号菜单去掉设置与资料行右箭头；关于 Sage 弹窗居中，未知版本信息使用破折号。新建专家只展示“暂未开放／可先与内置专家对话。”说明。

## 开发与维护边界

- 本包可直接复用 HTML/CSS/原生 DOM；实际 App 框架未提供，不能称为已接入原生 App。SwiftUI/Flutter 等按变量和结构映射。
- 后续修改先读包内 README.md 和 DESIGN_SPEC.md。只维护 tokens.json 和 catalog 源；用 `node build-tokens.cjs` 与 `node build-snippets.cjs` 生成输出，不分别维护重复数据。
- 53 张头像为当前已发布 WebP 原字节副本；不要混入历史 102 人素材。只有当前 8 位视觉样例使命经过本轮审阅，其余 45 位为 null，接入前补齐正式文案。
- 当前提供 UI 与本地交互样例；正式认证、任务、对话、用量、版本和权限由宿主接入。没有部署官网或更新 App 安装包。
- 16 张 PNG 为实测浏览器布局转 SVG 后的 2× 导出，保留 SVG 和测量数据；原生浏览器预览另存 JPEG。不得把样例状态当成真实业务结果。

本轮 `node validate.cjs` 通过：38 组件与片段、32 图标、53 头像摘要、16 张 3200×2048 页面稿、PNG CRC/摘要、源码语法及本地链接。28 组正文颜色对比最低 4.62。菜单/弹窗、复制、筛选、主题和焦点已做本地浏览器检查；实际 App 接入与 53 人匿名辨识测试仍待完成。
