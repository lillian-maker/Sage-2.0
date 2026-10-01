# Sage 2.0

独立中英文官网，并承接 WorldPilot 数字员工设计素材；不替代已有 Sage 仓库。

## 数字员工设计素材

完整素材保存在 [`digital-employees/`](digital-employees/README.md)，包含 102 位数字员工的深浅色头像、原始生成图、七种 WebP 尺寸、提示词、岗位数据、资源清单和展示组件。通过 `digital-employees/WorldPilot_Youth_Avatars.html` 预览。原始文件名和设计来源保持不变，不改写历史身份或岗位定义；新增 50 人的业务域与岗位配置状态以素材包说明为准。

2026-10-01 从 `lillian-maker/WorldPilot` 的 `2e676da87999c88fbc70baae9eb5dbf72c2b757e` 迁入 `digital-employees/` 全目录（1,971 个文件），逐文件 Git blob 校验一致，并核对头像清单哈希与尺寸资源字节数。后续维护素材前同时阅读该目录 README。官网现有内嵌头像和业务配置不受迁移影响。

## 开发与构建

使用 Python 3 标准库，无需安装构建依赖。

```sh
python3 build.py --release
python3 -m http.server 8896 --bind 127.0.0.1
```

中文入口 `index.html`，英文入口 `index-en.html`。`src/` 是可维护源码，`pages/` 和首页由构建生成，`dist/` 是发布目录。修改源码后重新构建，不直接编辑生成页面。

## 发布边界

- 官网是静态产品介绍和交互预览，不包含真实认证、支付、任务执行或咨询接收服务。
- 录屏按要求保留占位；下载页不会伪造安装包。
- 数字员工及协作为产品设计范围，不表示所有能力已可生产调用。
- 首页头像内嵌，避免独立转发 HTML 或图片目录漏传导致头像缺失。二级页的图片随发布包提供。
- 星空轻微错峰闪烁；系统减少动态效果设置下停止动画。
- 中英页面使用同一个地球图标语言菜单，保留当前页面、查询参数和锚点；只展示已发布语言。维护 `src/language-switch.html`，不要在各页面重复实现。
- `src/visual-draft.css` 为已确认正式视觉样式，沿用原文件名。

## 回归验证

已有 Playwright 和 Chrome 环境下：

```sh
SAGE_URL=http://127.0.0.1:8896 node tests/release-audit.cjs
```

如 Playwright 安装在其他位置，设置 `PLAYWRIGHT_MODULE`。测试覆盖中英首页45批头像、六种视口尺寸、标题与地球不重叠及导航页面。

维护前阅读本文件，保留中英文功能边界一致性；扩充词典时同步检查动态交互，不把内部状态键当作展示文案翻译。
