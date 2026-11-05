# Sage App 交互演示视频

当前版本：`assets/video/sage-team-collaboration.mp4`（47.5 秒，1440×900，H.264，约 2.9 MB）和海报图 `sage-team-collaboration-poster.jpg`。AI 团队页首屏和首页「看见 Sage 如何工作」共用这一条视频，只是章节划分不同。

## 制作方式

`render-team.cjs` 打开 Sage App 视觉稿 v5 的 HTML 源文件（`sage-all-screens-final.html`），把 6 个画面放进 1440×900 的舞台。它用脚本逐帧驱动真实 DOM 的状态变化，再逐帧截图，通过 ffmpeg 编码成视频。所以这是对设计稿原型的录制，不是对静态图片做镜头推拉：

| 章节 | 起点（秒） | 画面 | 画面里发生的变化 |
|---|---|---|---|
| 1 计划确认 | 0 | 33 计划确认卡 | 用户问题出现，主责专家逐字回复，执行计划逐行展开；光标点击“开始执行”，状态从“待你确认计划”变为“进行中” |
| 2 多专家编排 | 8 | 32 编排视图 | 节点依次从排队变为进行中、已完成，三位专家并行研究；需授权的节点停下并高亮；点开节点后详情抽屉滑出；进度和 Credits 数字随之变化 |
| 3 并行协作 | 17 | 57 并行工作卡 | 每位专家的计时实时增长，望野完成后交回主责汇总，等待人数从 3 变为 2 |
| 4 真人审核 | 24 | 09 协作 | 光标依次经过待你审批、待你补充、待你验收、邀请四类事项，点开审批后显示详情 |
| 5 授权把关 | 31 | 48 授权作用域 | 展开授权范围菜单（仅本次 / 本任务内 / 本工作区内），选择“仅本次”后批准；确认卡变为已批准，进度继续，出现可撤销提示 |
| 6 验收成果 | 39.5 | 28 成果预览 | 依次悬停引用角标 [1]、[3]，右侧对应资料同步高亮；点击“验收通过”后，版本从“草稿 v1”变为“已验收 v1” |

首页使用其中三段：提出问题与计划确认（0 秒）、专业岗位接力（8 秒）、交付与授权（31 秒）。修改章节时间时，需要同步修改 `src/pages/team.html` 和 `src/index.template.html` 中的 `data-start`。

企业服务页的协作截图 `assets/app/collaboration-inbox.webp` 由 `capture-stills.cjs` 从同一份 HTML 截取，命名规则与视频相同。

## 对外表述规则

- **第三方工具：** 视觉稿里的“Jungle Scout”在视频和截图中统一替换为“销量数据连接器”。目前没有证据表明这项集成已经接入；确认接入后，可以在 `render-team.cjs` 和 `capture-stills.cjs` 中取消替换。
- **标注：** 页面统一标注“Sage App 交互演示 · 设计稿 · 示例数据”，并注明界面与能力以 App 正式版本为准。英文页另外注明界面为中文。
- **数据：** 报告里的数字（德国本土品牌 6 席、€39–69 等）都是视觉稿里的示例数据。

## 仍未满足的前提

| 依赖 | 当前状态 | 影响 |
|---|---|---|
| App 视觉稿定稿 | v5 仍是“待用户确认” | 定稿后需要重录；如果选择器或文案有变，要同步修改脚本 |
| 真实 App 录屏 | App 主流程还没跑通 | 跑通后应改用真实录屏替换设计稿录制 |
| 英文界面 | 没有英文版设计稿 | 英文页暂时播放中文界面 |
| 字幕轨（WebVTT） | 未制作 | 目前读屏用户依赖章节说明文字和 `aria-label` |

## 重新生成

```sh
SAGE_APP_DESIGN=<视觉稿目录>/sage-all-screens-final.html \
FFMPEG=<含 libx264 的 ffmpeg> PLAYWRIGHT_MODULE=<playwright> \
node tools/app-demo-video/render-team.cjs

# 只预览某几个时间点的画面（不编码视频）：
PREVIEW=3,12.5,36 PREVIEW_DIR=/tmp node tools/app-demo-video/render-team.cjs

# 企业服务页截图：先截 PNG，再转成 WebP，宽度 1480
node tools/app-demo-video/capture-stills.cjs /tmp/stills
```

本机使用 `imageio-ffmpeg` 自带的 ffmpeg。默认 CRF 25，可以用环境变量 `CRF` 调整。构建时会自动更新资源的内容哈希。
