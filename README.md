<p align="center">
  <img src="assets/brand/logo.png" width="112" alt="ACE Scheduler Logo">
</p>

<h1 align="center">ACE Scheduler</h1>

<p align="center">看清资源变化，按需调整调度。</p>
<p align="center">A Windows desktop utility for observable, reversible CPU scheduling.</p>

<p align="center">
  <a href="https://github.com/chenlaoc/ace-scheduler/actions/workflows/ci.yml"><img src="https://github.com/chenlaoc/ace-scheduler/actions/workflows/ci.yml/badge.svg" alt="Windows CI"></a>
  <br>Windows x64 · Python 3.11+ · PySide6 · v1.5.0（开发版）
</p>

<p align="center">
  <a href="https://github.com/chenlaoc/ace-scheduler/releases">下载版本</a> ·
  <a href="docs/USER_GUIDE.md">使用指南</a> ·
  <a href="docs/DEVELOPMENT.md">开发指南</a> ·
  <a href="docs/TESTING.md">测试与验证</a> ·
  <a href="CHANGELOG.md">更新记录</a>
</p>

ACE Scheduler 用于管理 Windows **用户态进程**的 CPU 调度策略。它把实时监控、批量配置、应用前后对照和原状态恢复放在同一个桌面工作空间中，内置 ACE 相关进程名称，也可添加精确的 `.exe` 名称。

每次启动先观察，点击应用后才改变调度。保存规则、选择预设和切换页面不会隐式应用；修改结果会通过 Windows API 回读确认。

![运行概览，示例数据](docs/images/overview.png)

> 截图使用明确标注的模拟数据，仅展示界面，不表示游戏性能或 I/O 改善结论。

## 一个工作空间，五个模块

| 模块 | 可以做什么 |
| --- | --- |
| **运行概览** | 选择具体进程实例，观察整机归一化 CPU%、内存、进程 I/O 和实时调度值 |
| **调度策略** | 直接逐行修改，或勾选多行统一设置 Priority、CPU Affinity、EcoQoS 与维护方式 |
| **实验对照** | 冻结应用前基线，与应用后最近 60 秒对照，导出 CSV |
| **设置与日志** | 采样与关闭偏好、异常恢复、后台重启及脱敏诊断 |
| **关于** | 版本与构建信息、私有仓库更新/反馈入口、配置目录和许可 |

![批量调度策略](docs/images/policy.png)

窗口采用应用内标题栏，支持拖动、边缘缩放、双击最大化和内嵌窗口按钮。编辑草稿在切页和后台刷新时保留；底部批量操作栏固定可见。

## 快速开始

### 使用桌面程序

1. 从 [Releases](https://github.com/chenlaoc/ace-scheduler/releases) 下载 `ACE-Scheduler-vX.Y.Z-x64.zip`，解压整个目录。
2. 运行 `ACE-Scheduler.exe`；保留旁边的 `_internal` 文件夹。
3. 在运行概览观察基线，到调度策略配置并点击“应用勾选”。结束实验时选择恢复原设置。

常规启动可观察和编辑，点击需要写入的操作时才请求管理员权限。取消会保留窗口和草稿；接管后仍只观察，请再次点击所需操作。也可以明确只读启动：

```powershell
.\ACE-Scheduler.exe --monitor-only
```

当前发行包面向 Windows 11 x64；Windows 10、Windows ARM64 和跨 Processor Group 的 Affinity 不属于已实测发行范围。源码要求 Windows、64 位 Python 3.11+。受保护进程可能拒绝正常 API 操作，程序会如实显示结果。

### 从源码启动

```powershell
git clone https://github.com/chenlaoc/ace-scheduler.git
cd ace-scheduler
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m ace_scheduler --monitor-only
```

仓库是私有的，克隆与下载需要仓库访问权限。开发环境、构建命令和模块说明见 [开发指南](docs/DEVELOPMENT.md)。

## 调度方式

| 预设 | Priority | CPU Affinity | EcoQoS |
| --- | --- | --- | --- |
| 默认 | Normal | 全部可用逻辑 CPU | OFF |
| 温和 | Below Normal | 最后 25%，至少 2 个，不超过可用总数 | ON |
| 较强 | Idle | 最后 1 个可用逻辑 CPU | ON |
| 自定义 | 按行选择 | 自选或动态集合 | 按行选择 |

- **仅应用一次**：本会话中每个进程实例应用一次，进程重启后识别新实例。
- **持续维护**：独立计时器检查实际值，只在不符合设置时重新写入。
- **停止规则**：停止后续自动应用，保留当前值。
- **恢复原设置**：恢复当前或未完成会话中，该实例首次修改前记录的值；外部修改默认视为冲突。默认预设与恢复原设置有不同含义。

“最后一个 CPU”指逻辑编号，不代表效率核。25% 是选取的逻辑 CPU 数量比例，不是 CPU 使用率硬上限。多组或超过 64 个逻辑 CPU 时禁用 Affinity，保留监控、Priority 与 EcoQoS。

## 项目边界

本项目通过 Windows 正常接口进行调度实验，没有进程终止/挂起、服务管理、驱动修改、代码注入或反作弊绕过功能，不修改 I/O Priority，也不包含第三方参考程序的二进制或源码。

进程 I/O 不等于 SSD 物理读取，CPU 调度也不等于磁盘限速。项目没有承诺帧率提升、磁盘寿命改善或与全部游戏兼容。指标口径、实例匹配和恢复语义见 [使用指南](docs/USER_GUIDE.md)，实现依据见 [架构文档](docs/ARCHITECTURE.md)。

## 配置与升级

配置和轮转日志继续保存在 `%APPDATA%\ACE-CPU-Scheduler`。v1.4.0 将品牌名称统一为 **ACE Scheduler**，沿用旧目录和实例锁，升级后无需迁移规则。v1.5.0 在每次写入前保存恢复记录；异常重开后由用户选择恢复。系统重启或记录超过 30 天后归档，不跨实例恢复。恢复前会重新核验身份和当前值。

关闭按钮默认退出，可在设置中改为隐藏到托盘。托盘的“停止全部规则”保留现值，“真正退出”继续经过恢复选择。诊断 ZIP 只导出脱敏摘要，不上传。具体边界与验证见 [本阶段实现记录](docs/DAILY_USABILITY.md)。

## 质量与协作

Windows CI 在 Python 3.11 / 3.13 上运行测试，并单独构建 EXE、验证只读启动、产出 ZIP 与 SHA-256。Windows 写入测试仅操作测试自行创建的子进程。

- [测试策略与复现方法](docs/TESTING.md) · [31 组交互测试记录](docs/INTERACTIVE_TEST_PLAN.md)
- [贡献规范](CONTRIBUTING.md) · [问题反馈](https://github.com/chenlaoc/ace-scheduler/issues/new/choose)
- [安全问题报告](SECURITY.md) · [维护与发布](docs/RELEASING.md) · [路线图](docs/ROADMAP.md)
- [品牌资产与生成记录](docs/BRANDING.md) · [第三方声明](THIRD_PARTY_NOTICES.md)

项目由 [@chenlaoc](https://github.com/chenlaoc) 维护，当前为私有项目，原始项目代码与品牌保留全部权利；访问仓库不等同于获得再分发许可。第三方组件按各自许可使用，详见 [LICENSE](LICENSE)。
