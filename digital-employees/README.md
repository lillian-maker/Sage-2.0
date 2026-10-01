# WorldPilot 青年数字员工头像 · Collection 06

完整 102 位员工，深色与浅色各一套。人物采用用户确认的年轻、清爽、带轻微插画特征的数字同事形象，以端正自然的头部姿态为主，通过脸型、发型、肩部朝向、视线和表情区分人物。

## 文件内容

| 路径 | 用途 |
| --- | --- |
| `WorldPilot_Youth_Avatars.html` | 离线预览；可切换明暗、搜索岗位、比较两套、下载单张 WebP |
| `WorldPilot_Youth_Avatars_Dark.jpg` | 深色原始 50 人总览 |
| `WorldPilot_Youth_Avatars_Light.jpg` | 浅色原始 50 人总览 |
| `WorldPilot_New50_Avatars_Dark.jpg` | AGT-053—AGT-102 深色新增 50 人总览 |
| `WorldPilot_New50_Avatars_Light.jpg` | AGT-053—AGT-102 浅色新增 50 人总览 |
| `assets/dark/AGT-001.png` | 深色原生尺寸单人 PNG，其他员工依 ID 命名 |
| `assets/light/AGT-001.png` | 同一员工的浅色 PNG |
| `assets/{dark,light}/{32,48,64,80,128,256,512}/AGT-001.webp` | 7 种常用尺寸 WebP |
| `sources/AGT-001.png` | 内置图像生成的原始双联源图，左深色、右浅色 |
| `workforce.json` | 全部岗位原始数据：ID、姓名、职责、业务域、数量与标准产物 |
| `avatar-manifest.json` | 逐人资源路径、尺寸、哈希、完整提示词与生成信息 |
| `avatar-index.csv` | 便于设计和开发对接的路径索引 |
| `design/AGT-001.prompt.txt` | 每个员工的完整生成提示词 |
| `design/generation-brief.txt` | 统一风格与姿态规范 |
| `avatar.css`、`Avatar.jsx` | 可选的展示样式与 React 接入示例 |

## 使用建议

头像文件为方形。产品中的圆形效果用 `border-radius: 50%` 实现，圆角方形用 `20%`；两者使用相同素材。建议列表使用 32–48 CSS px、员工卡片 64–80 CSS px、详情 128–256 CSS px。高密度屏幕可选约两倍尺寸的 WebP。

依据产品当前主题选择 `assets/dark/` 或 `assets/light/`，保持员工 ID 不变即可。深色产品背景推荐中性炭灰，浅色推荐纯白或冷白；头像已有对应的低饱和底色。

图片通过内置 image_gen 生成。导出仅进行双联图等分切割、缩放及格式转换，没有程序重画人脸或修改底色。原始生产源图、两套独立 PNG 和所有 WebP 尺寸均保留。

这是静态形象素材集，头像清单现含 102 位员工；AGT-053—AGT-102 的岗位与业务域仍待后续配置。两组 JPG 分别展示原始 50 人和新增 50 人，完整 102 人请使用 HTML 预览。此素材包不包含动态表情帧，也未改写现有官网或产品页面的业务配置。

## React 示例

```jsx
import Avatar from './Avatar.jsx';
import './avatar.css';

<Avatar id="AGT-001" name="衡远" theme="dark" size={48} base="/assets" />
```

将 `assets/dark` 与 `assets/light` 放在示例中 `base` 所对应的位置。`Avatar.jsx` 是独立接入示例，不依赖现有 WorldPilot 项目的内部组件。
