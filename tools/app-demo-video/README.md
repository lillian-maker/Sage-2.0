# Sage App 交互演示视频

依据 2026-11-06 的 App PRD（D75、D76）与 Figma 文件 `dnmr3E1NLP19pX3X3ZSRxo`（与 `sage-all-screens-final.html` 一一对应）录制两条视频，分工不同、互不复用画面：

| 视频 | 位置 | 讲什么 | 章节（起点秒） |
|---|---|---|---|
| `sage-home-demo.mp4`（52 秒） | 首页「看见 Sage 如何工作」 | 和 Sage 一起工作的差异化体验 | 先澄清再开工 0 · 多任务一屏掌握 9.5 · 只推需要你决定的事 16 · IM 接入两层过滤 23.5 · 逐条核对验收 31 · 越用越顺手 39 |
| `sage-team-collaboration.mp4`（50.5 秒） | AI 团队页首屏 | 专家团队如何组织与协作 | 专家目录 0 · 协作网络 6.5 · 专家团队 13.5 · 多专家编排 20 · 主责统一汇报 29 · 授权把关 35.5 · 版本留痕 44 |

`render-demo.cjs` 打开设计源文件，把场景画面放进 1440×900 舞台，逐帧驱动真实 DOM 状态（点选、状态流转、计数、弹窗、高亮）后截图，用 ffmpeg 编码（CRF 27）。场景在 `SCENES` 中按画面定义，两条视频的组合在 `VIDEOS` 中。修改章节时同步修改 `src/index.template.html` 与 `src/pages/team.html` 的 `data-start`。

企业服务页「企业版 App」的 6 张截图（收件、工作区规则、成员与权限、企业知识库、知识图谱、企业管理）由 `capture-stills.cjs` 从同一份源文件截取，保存为 `assets/app/enterprise-*.webp`。

## 对外表述规则

- **第三方工具：** 视觉稿里的“Jungle Scout”“Keepa”在视频和截图中分别替换为“销量数据连接器”“价格数据连接器”。目前没有证据表明这些集成已经接入；确认接入后，可以在 `render-demo.cjs` 的 `GENERIC` 和 `capture-stills.cjs` 中取消替换。
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
DEMO=home SAGE_APP_DESIGN=<设计目录>/sage-all-screens-final.html FFMPEG=<ffmpeg> PLAYWRIGHT_MODULE=<playwright> node tools/app-demo-video/render-demo.cjs
DEMO=team ...（同上）
# 只看某些时间点：PREVIEW=3,12 PREVIEW_DIR=/tmp；只看某个场景：SCENES=wake:3
node tools/app-demo-video/capture-stills.cjs /tmp/stills   # 再转为宽 1480 的 WebP
```

本机使用 `imageio-ffmpeg` 自带的 ffmpeg。当前版本用 `CRF=27` 编码（默认 25），可以用环境变量 `CRF` 调整。构建时会自动更新资源的内容哈希。
