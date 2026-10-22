---
title: Sage App UI 开发交付入口
date: 2026-10-22
edition: 0.1
status: 设计与前端组件交付，待产品验收
scope: 当前 Sage 桌面 App 的双主题视觉与可复用 Web 组件；真实业务由 App 接入
---

# Sage App UI 开发交付

同伴开发先读本文件，再看 [设计规范](DESIGN_SPEC.md)、[组件预览](index.html)和[双主题页面设计稿](app.html?view=experts&theme=light)。本包覆盖当前规范中的 **38 类组件、32 个 SVG 图标、53 位数字员工头像**，提供对应 HTML、CSS、原生 DOM 交互与设计变量。

这套代码适合 Web、Electron 等使用网页界面的容器直接复用。实际 App 框架尚未提供；原生 SwiftUI、Flutter 等界面应按 `tokens.json` 和组件结构映射，不能将 Web 源码视为已接入原生 App。

## 快速入口

| 内容 | 入口 |
| --- | --- |
| 18 张双主题页面图 | [visuals/index.html](visuals/index.html) |
| 38 类组件与可复制 HTML | [index.html](index.html) |
| 浅色产品页 | [app.html?view=experts&theme=light](app.html?view=experts&theme=light) |
| 纯黑产品页 | [app.html?view=experts&theme=dark](app.html?view=experts&theme=dark) |
| 完整设计规范 | [DESIGN_SPEC.md](DESIGN_SPEC.md) |
| 53 人头像、尺寸与匿名对照 | [avatars.html](avatars.html) |
| 设计变量 | [tokens.json](tokens.json) |
| 组件清单与状态 | [components.json](components.json) |
| SVG symbol 集 | [icons.svg](icons.svg) |
| 头像映射与校验 | [assets/avatars/manifest.json](assets/avatars/manifest.json) |
| 设计总览 | [visuals/design-system-overview.png](visuals/design-system-overview.png) |

## 文件结构

```text
README.md                 开发接入入口、API 与业务边界
DESIGN_SPEC.md            当前设计规范和待验收建议
index.html                组件目录、状态、HTML 复制入口
app.html                  专家/资料/对话/新任务/账号页面稿
avatars.html              53 人头像及 32/48/64/88/128px 对照
tokens.json               颜色、字体、尺寸、空间、动效的变量源
tokens.css                从 tokens.json 生成，禁止直接维护
build-tokens.cjs           重生成 tokens.css 的零依赖脚本
components.css            共享组件样式
components.js             主题、菜单、弹窗、标签页、轻提示的原生 DOM 交互
components.json           38 类组件及变体清单（从目录生成）
snippets/                 38 个可复制 HTML 局部片段
build-snippets.cjs         从 index.html 生成片段和组件清单
screens.css               产品页面组合样式，不是基础组件依赖
employees.js              员工 ID/姓名/岗位/头像与有限的视觉样例文案
icons.svg                 32 个 SVG symbol
assets/brand/             正式品牌 SVG：浅底、深底、独立图形
assets/avatars/            53 张当前 512×512 WebP 及 manifest
visuals/                  亮/暗页面导出与设计总览
```

保留本目录结构复制或下载，避免只转发一个 HTML 导致头像、图标和样式缺失。用本地 HTTP 服务预览；部分浏览器会限制 `file://` 页面引用外部 SVG symbol。

```sh
python3 -m http.server 8915 --bind 127.0.0.1
```

在本目录执行后打开 `http://127.0.0.1:8915/`。不需要安装依赖或运行打包器。修改变量后执行 `node build-tokens.cjs`；只改变量源，不手改生成文件。组件样例源在 `index.html` 的 catalog 数据中；修改后运行 `node build-snippets.cjs`，不要分别维护生成的 `snippets/` 和 `components.json`。

独立 HTML 片段位于 [snippets/](snippets/)，可直接复制到宿主界面；它们是局部片段，资源路径以 UI kit 根目录为基准，不作为独立 HTML 页面打开。

## 最小接入

基础组件只需要 `tokens.css`、`components.css`、`components.js` 三个文件；使用图标、人物或 Logo 时同时保留对应资产。`screens.css` 仅供复用本包页面排版。

```html
<!doctype html>
<html lang="zh-CN" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="stylesheet" href="tokens.css">
  <link rel="stylesheet" href="components.css">
  <script src="components.js" defer></script>
</head>
<body class="sage-ui">
  <button class="sg-button" type="button">
    <svg class="sg-icon" aria-hidden="true" viewBox="0 0 24 24">
      <use href="icons.svg#chat"></use>
    </svg>
    对话
  </button>
</body>
</html>
```

组件使用 `.sg-*` 类；`.sage-ui` 提供字体、基础盒模型和控件继承。主题变量默认在根元素生效；需要局部主题时，可在包含组件的 `.sage-theme` 容器上设置 `data-theme="light"` 或 `data-theme="dark"`。正文和按钮颜色使用语义变量，不从导出图重新采色。

脚本会在 DOM 就绪时自动初始化文档级委托事件，不需要重复调用 `init()`，也不提供 `init(子容器)` 接口。宿主渲染动态组件时保持 ID 唯一，并遵循相同的 `data-*` 和原生元素结构。

## 组件范围

| 类别 | 包含组件 |
| --- | --- |
| 基础与操作 · 6 | 按钮、图标按钮、线性图标、员工头像、工作风格标签、状态标记 |
| 输入 · 7 | 单行输入、多行输入、下拉选择、复选框、单选框、开关、搜索框 |
| 导航 · 5 | 筛选与标签页、侧栏导航、面包屑、分页、下拉菜单 |
| 内容 · 5 | 基础卡片、数字员工卡片、数据表格、折叠面板、附件条目 |
| 反馈 · 6 | 就地提示、轻提示、进度条、骨架占位、空结果、错误状态 |
| 浮层 · 4 | 悬停说明、锚定说明、通用弹窗、关于 Sage |
| 协作 · 5 | 对话消息、对话输入区、登录与注册、账号与菜单、试用提示条 |

合计 38 类，逐项变体以 `components.json` 和组件页为准。“完整”指当前设计规范的组件范围，不代表未来模块的所有业务组件。

原有 21 个图标保持，按界面需要补齐 11 个：`close`、`send`、`attachment`、`copy`、`download`、`back`、`more`、`menu`、`chevron-up`、`eye`、`refresh`。统一使用 24×24 viewBox、1.6px 描边、圆角端点，标准显示 20px，对话按钮内显示 17px。纯图标按钮必须有可访问名称。

## 双主题页面稿

页面组合共 9 种状态，浅色与纯黑各一套，已导出 **18 张 3200×2048 PNG**，并保留 SVG 与布局测量数据；逐图文件与哈希见 [visuals/manifest.json](visuals/manifest.json)。

| 页面/状态 | 浅色 | 纯黑 |
| --- | --- | --- |
| 专家目录 · 三列 | [预览](app.html?view=experts&columns=3&theme=light) | [预览](app.html?view=experts&columns=3&theme=dark) |
| 专家目录 · 四列 | [预览](app.html?view=experts&columns=4&theme=light) | [预览](app.html?view=experts&columns=4&theme=dark) |
| 员工资料 | [预览](app.html?view=profile&employee=MGMT-CEO&theme=light) | [预览](app.html?view=profile&employee=MGMT-CEO&theme=dark) |
| 对话工作台 | [预览](app.html?view=chat&employee=MGMT-CEO&theme=light) | [预览](app.html?view=chat&employee=MGMT-CEO&theme=dark) |
| 新任务 | [预览](app.html?view=task&theme=light) | [预览](app.html?view=task&theme=dark) |
| 登录 / 注册 | [预览](app.html?view=signin&auth=guest&theme=light) | [预览](app.html?view=signin&auth=guest&theme=dark) |
| 未登录专家目录 | [预览](app.html?view=experts&auth=guest&theme=light) | [预览](app.html?view=experts&auth=guest&theme=dark) |
| 账号菜单 | [预览](app.html?view=experts&overlay=account&theme=light) | [预览](app.html?view=experts&overlay=account&theme=dark) |
| 关于 Sage | [预览](app.html?view=experts&overlay=about&theme=light) | [预览](app.html?view=experts&overlay=about&theme=dark) |

`capture=1` 是固定 1600×1024 导出画布，隐藏评审工具栏；日常交互预览不使用此参数。三/四列选项受窗口可用宽度约束，窄窗会减少列数。页面稿与组件源码使用同一套共享样式。

## 原生 DOM API

所有函数位于 `window.SageUI`，没有框架、网络或账号依赖。

| API | 参数与行为 |
| --- | --- |
| `setTheme(theme, root?)` | `theme` 为 `light`、`dark`、`system`；默认根为 `document.documentElement`。设置 `data-theme` 为实际主题、`data-theme-preference` 为用户选择，更新主题控件与品牌图片并触发主题事件。未知主题会抛错。 |
| `openDialog(id, trigger?)` | 打开 ID 对应的原生 `<dialog>`，关闭菜单并聚焦弹窗；保存可见触发入口供关闭后返回。默认入口为当前焦点。 |
| `closeDialog(idOrElement)` | 关闭对应原生 `<dialog>`。不存在或不是 dialog 的目标会抛错。 |
| `toast(message, tone?)` | 显示短提示；默认 `info`，推荐 `info/success/warning/danger`。危险提示约 5 秒，其余约 3.2 秒。需要处理的问题应改用就地提示。 |
| `init()` | 文档级幂等初始化；默认自动执行。无子容器参数。 |

`setTheme()` 不保存账号偏好，不调用账号接口，也不写持久化存储。宿主如需记住用户选择，应处理 `sage:themechange` 的 `theme`；路由跳转保留 `system`，不要用解析后的实际 light/dark 覆盖用户选择。

### HTML `data-*` 约定

| 属性 | 配套结构与作用 |
| --- | --- |
| `data-variant` | `.sg-button` 的样式变体：默认、`primary`、`secondary`、`text`、`danger`。 |
| `data-loading="true"` | `.sg-button` 加载外观；同时添加 `disabled` 阻止重复操作。 |
| `data-tone` | 提示、标记的 `info/success/warning/danger` 语义色；不是业务状态来源。 |
| `data-theme-value` | 主题按钮值；点击应用 light/dark/system。 |
| `data-theme-label` | 当前主题名称占位，由主题函数更新。 |
| `data-theme-logo` | 品牌 `<img>` 标记；配套 `data-logo-light` 与 `data-logo-dark` 两条相对图片路径。 |
| `data-menu-toggle="ID"` | 菜单或说明气泡触发按钮；配套目标 `id`、初始 `hidden`、`aria-controls`、`aria-expanded`；真实菜单加 `aria-haspopup="menu"`。 |
| `data-open-dialog="ID"` | 打开目标原生 dialog。 |
| `data-close-dialog` | 关闭按钮放在目标 dialog 内。 |
| `data-tabs` | 标签页容器；内部按钮使用 `role="tab"`、`aria-selected`、`tabindex`，有面板时提供唯一 `aria-controls`。 |
| `data-value` | 标签页触发事件时的稳定值；不要以展示文案代替业务键。 |
| `data-clear="ID"` | 清空目标输入并触发 `input`，焦点回到输入框。 |
| `data-copy-target="ID"` | 复制目标元素的文本；复制失败展示明确提示，不静默报成功。 |
| `data-toast` | 展示样例提示，不执行真实业务操作。 |
| `data-composer` | 使用原生 `<form>`；提交时读取内部 `<textarea>`，仅触发 `sage:send`。空输入返回输入焦点。 |
| `data-login-example` / `data-composer-example` | 本包静态样例标记，阻止普通表单提交；不要直接作为生产登录表单逻辑。 |

标准 checkbox、radio、select、details/summary 使用原生交互。自定义 menu 的项使用 `role="menuitem"`，Esc 返回可见入口；上下方向键与 Home/End 移动菜单焦点。标签页支持左右方向键与 Home/End。对话框使用原生模态行为；关闭按钮、Esc 和遮罩关闭均须验收。

### 宿主监听事件

```js
document.addEventListener('sage:themechange', event => {
  const {theme, actual} = event.detail;
  // theme 是用户选择；actual 是当前解析出的 light 或 dark。
  // 在宿主中接入偏好存储；不要把 system 改写为 actual。
});

document.addEventListener('sage:tabchange', event => {
  const {value, tab} = event.detail;
  // 按稳定 value 更新宿主数据或筛选；tab 是触发的按钮元素。
});

document.addEventListener('sage:send', event => {
  event.preventDefault();
  const {text, form} = event.detail;
  // 在此交给宿主的消息服务；本组件本身不发送网络请求。
  // 宿主应处理加载、失败、重试，并在明确成功后决定是否清空草稿。
});
```

事件均冒泡；`sage:send` 可取消，detail 为 `{text, form}`。组件默认不会把文字当作 HTML 插入，也不会生成真实回复。页面稿中添加本地消息的逻辑是版式演示，不应照搬为后端完成状态。

## 人物资产与内容边界

53 张头像均为当前已发布的 **512×512 WebP 原字节副本**，本次没有重新修图、转码或调整脸部。`assets/avatars/manifest.json` 记录副本的 `sha256` 和原始 PNG 的 `sourceSha256`，两者不是同一格式文件的摘要，不应相互比较为一致。

ID 是身份映射唯一键；不要按排序猜人物，不要将历史 102 人素材混入当前 53 人。`employees.js` 提供 ID、姓名、岗位、相对资源路径、尺寸、摘要、类型及文案状态。

**只有当前 8 位视觉样例的使命经过本轮文案审阅。** 其余 45 位的 `mission` 为 `null`，不能替换成通用句或把这份数据称为完整 53 岗位使命文案。接入全部员工目录前，补齐并审阅正式使命；未知交付物与其他能力不由头像或页面样例推断。

头像页提供 32/48/64/88/128px 查看和匿名模式，方便评审身份区别。该工具不是“53 人已通过辨识测试”的证据；后续应隐藏文字，独立检查同一员工在小尺寸中是否稳定可认。

## 业务边界与接入责任

| 当前交付 | 宿主 App 需要接入 |
| --- | --- |
| 原生 UI 状态、主题、键盘焦点、菜单、弹窗、筛选样例 | 正式路由、数据加载、权限和偏好持久化 |
| 登录/注册布局与账号菜单外观 | 身份认证、注册、退出、隐私协议和账号资料 |
| 使命、资料与对话版式 | 正式岗位文案、真实对话服务、任务状态与交付物 |
| 附件条目与选择入口外观 | 文件选择、上传、访问控制和失败恢复 |
| 用量“待同步”、版本 `—` | 套餐与额度事实、版本、构建、发布日期与更新检查 |
| 新建专家灰态入口说明 | 创建能力开放后的独立产品规则及实际创建流程 |

源码未包含登录、计费、任务或对话的网络调用。新建专家当前只能显示“暂未开放／可先与内置专家对话。”，没有真实创建动作。不将设计样例与生产完成状态混用。

<a id="design-baseline"></a>
## 设计基线与维护

后续修改先读本 README 与 DESIGN_SPEC.md。当前规则：浅蓝侧栏＋白色内容；深色主背景 `#000000`；对话按钮深色文字 `#14243A`；职位优先、使命完整且常规字重；头像进入资料，卡片只有“对话”。账号菜单不含设置，资料行没有右箭头；人物身份与最新原资产保持。

色值源为 `tokens.json`；新增变量先核对是否已有语义变量，禁止为截图抗锯齿采样色新增 token。图标只在统一描边规则内补充，组件以当前清单为范围。对图片仅做展示缩放，不修改人物身份。

设计规范保留“用户已确认／本次补齐建议／真实 App 实现”三层边界。响应式断点、更多状态与小尺寸辨识评审的通过情况以对应新鲜验证记录为准。发布设计目录不等于部署官网或升级实际 App。

## 高清图导出

`visuals/` 页面稿由浏览器实测布局生成 SVG，再以 2× 导出 PNG，不是浏览器截图。源头像 WebP 保持原字节；为兼容 SVG 渲染器，导出时在内存中转换为 PNG 表示，已核对解码后的 RGBA 像素哈希一致。胶囊半径按组件高度裁定，头像继承浏览器测得的容器圆角。导出工具 `export-visuals.cjs` 使用现有 Sharp，运行 UI 预览本身不需要它。
