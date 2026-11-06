# Sage 数字员工头像（五套）

五套头像与五种主题风格一一对应（D56），统一按证件照构图（D72）。官网默认使用写实一套（同 `assets/people/`）。

| 目录 | 主题风格 | 构图 |
|---|---|---|
| `photo/` | 商务 · 稳重（写实） | head 60% of height, headroom 8% (crown y=0.08, chin y=0.68), centered |
| `iso/` | 新锐 · 电光（等距体块） | head 50% of height, headroom 10% (crown y=0.10, chin y=0.60), centered |
| `collage/` | 时尚 · 编辑（编辑拼贴） | head 50% of height, headroom 10% (crown y=0.10, chin y=0.60), centered |
| `flat/` | 舒适 · 易读（平涂插画） | head 50% of height, headroom 10% (crown y=0.10, chin y=0.60), centered |
| `line/` | 极简 · 专注（线性符号） | head 50% of height, headroom 10% (crown y=0.10, chin y=0.60), centered |

每套 53 张，512×512 WebP，文件名为员工 ID。逐张哈希见 `manifest.json`。前端用 `object-fit: cover` 填满圆形或圆角方形容器，不要逐张裁切或偏移。
