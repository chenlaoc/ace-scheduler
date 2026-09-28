# ACE Scheduler 品牌资产

正式项目名：**ACE Scheduler**。GitHub slug / Python 模块：`ace-scheduler` / `ace_scheduler`。Windows 可执行文件：`ACE-Scheduler.exe`。

Logo 使用蓝青色交叠带组成 A，深蓝圆角底板让它在浅色和深色任务栏上都能辨认。视觉对应 CPU 调度中的并行、秩序与节奏，沿用界面的蓝色与玻璃材质。

## 文件与使用

- `assets/brand/logo.png`：生成的 PNG 主文件，四角包含透明通道；README、窗口和侧栏共用。
- `assets/brand/version_info.txt`：Windows 文件属性中的产品名和版本信息。
- 构建时 PyInstaller 将 PNG 编码为 Windows 图标并嵌入 EXE；不依赖网络加载。
- README 使用 112 px 展示，应用侧栏使用 52 px。保持长宽比，不拉伸、添加文字或改变主体颜色。
- 配色：深蓝 `#10243E`、蓝 `#2588FF`、青 `#5AE0E5`。

该标识是项目独立生成的视觉资产，与腾讯或其他软件品牌无官方关联。品牌命名不改变原配置目录 `%APPDATA%\ACE-CPU-Scheduler`，以便旧版升级继续使用同一份规则与实例锁。

## 生成记录

2026-09-28，使用 Codex 内置 image_gen 工具生成；未调用外部 API 脚本。以下为最终生成提示词，保留用于复现设计意图（生成结果不保证逐像素一致）。

```text
Use case: logo-brand. Create the final application logo for ACE Scheduler, a precise Windows CPU scheduling utility. Deliver one standalone square app icon, no text, no mockup, no contact sheet. A memorable geometric capital A sculpted from two broad interlocking blue and cyan glass ribbons, with a small precise negative-space crossbar suggesting aligned scheduling lanes; strong readable silhouette at 32px. The A sits on an opaque deep midnight-navy rounded-square tile with generous internal margins (mark occupies about 60% of tile). Subtle iOS-inspired polished translucent material, restrained edge highlights and smooth tonal gradients, nearly flat front view, sophisticated developer-tool identity. Tile fills 90% of a 1024x1024 square canvas and is centered, transparent background outside the rounded-square tile. Clean crisp geometry, symmetrical balance, premium but quiet. Palette midnight #10243E, electric blue #2588FF, cyan #5AE0E5. No letters other than the single abstract A mark, no words, no watermark, no shield, no lock, no weapons, no gaming mascot, no particles, no floor or cast shadow outside tile. Genuine alpha transparency outside the tile.
```
