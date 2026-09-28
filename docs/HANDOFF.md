# ACE Scheduler：下一阶段交接

> 后续进展：本文以下保留 v1.4.0 交接时的历史事实。恢复记录、托盘、按需提权、诊断与关于页面已在 `codex/daily-usability` 分阶段实现，当前代码为尚未发布的 v1.5.0；继续开发前先读 [实现与验证记录](DAILY_USABILITY.md) 并核对实际 Git 状态。

更新时间：2026-09-28。本文记录本次会话结束时的事实、用户确认的方向与建议实施顺序；接手时以实际代码和 Git 状态为准。

## 1. 用户意图与本次范围

用户希望把 ACE Scheduler 推进为成熟的 Windows App，已同意下一阶段优先补齐：**异常恢复记录、托盘与退出流程、按需提权、诊断导出、关于页面**。安装、更新与签名随后推进。

本次用户要求先做好交接，由其自行到新会话继续。上述新功能尚未实施，不要把方案当作已有能力。用户此前要求并已完成：iOS 26 风格的模块化界面、逐行/批量策略编辑、应用内标题栏、交互验证、独立 Logo 和私有 GitHub 仓库建设。

用户偏好：中文交流；基于现有项目直接推进，提供可核查的结果；避免重复确认已经授权的常规开发动作。遇到真实架构取舍时说明依据，购买签名服务、改变仓库可见性等另作决策。

## 2. 已交付基线

- 项目：ACE Scheduler；Python 模块 `ace_scheduler`；Windows 程序 `ACE-Scheduler.exe`。
- 私有仓库：<https://github.com/chenlaoc/ace-scheduler>；默认分支 `main`。
- 功能基线：`v1.4.0`，提交 `7145bff468715975c5262e62f7abda50d5572092`。
- 本次交接开始时工作区干净，HEAD 为上述提交。本次新增/更新的交接与路线图文档保留在本地工作区，尚未提交；接手时一并检查，勿误当作应用功能改动。
- 技术栈：Python 3.11+、PySide6-Essentials、psutil、ctypes/Win32、PyInstaller onedir；本机 `.venv` 使用 Python 3.13.2。
- 四个模块：运行概览、调度策略、实验对照、设置与日志；保留当前视觉风格、批量操作和无系统标题栏的窗口行为。
- 品牌资产：`assets/brand/logo.png`、`assets/brand/version_info.txt`；生成提示词见 [品牌记录](BRANDING.md)。
- 用户配置与实例锁仍在 `%APPDATA%\ACE-CPU-Scheduler`，这是升级兼容要求，不要仅为统一名称而更换目录。

### 已验证的结果

- [Windows CI](https://github.com/chenlaoc/ace-scheduler/actions/runs/36382069965)：Python 3.11、3.13 各 **103 项通过、0 跳过**；构建与只读启动验收通过。
- [Release 工作流](https://github.com/chenlaoc/ace-scheduler/actions/runs/36382324268)：同一提交通过测试、版本校验、打包和启动验收。
- [v1.4.0 Release](https://github.com/chenlaoc/ace-scheduler/releases/tag/untagged-f086b3e5cf1b8b6d27d2) **仍为 Draft**，本次交接时已重新查询确认，尚未正式发布。
- Release ZIP 已下载并在本机 Windows 11 解压启动验证：版本、Logo、四页截图、原生窗口框架、只读模式均正常。
- 发行 ZIP：`dist/ACE-Scheduler-v1.4.0-x64.zip`，39,719,981 字节。
- SHA-256：`9c80ca6f40a9e9ee3b67bd55bbe39bb7d6491a49485ad2a7a236450cf43f13fc`。
- `dist/ACE-Scheduler/` 是较早的本地构建；与 GitHub 发行包核对时应以以上 ZIP 和包内 manifest 为准。
- 本地证据：`artifacts/github-verification/`；实机发行包报告为 `native-release/result.json`。这些目录被 Git 忽略，重新克隆仓库时不会自带。

### 仓库与发行限制

已配置 README、使用/开发/测试/架构文档、Issue/PR 模板、CODEOWNERS、标签、Dependabot、SHA 固定的 Actions、自动打包与手动草稿发布。普通 Actions 默认只读；未启用自动合并。

当前账号套餐无法为私有仓库启用分支保护，GitHub 返回 403，见 [维护与发布](RELEASING.md)。仓库必须保持私有。程序还没有代码签名证书，不可宣称已经签名或获得 SmartScreen 信任。

## 3. 现有行为及不可破坏的约束

1. **启动只观察；保存、编辑和切页不隐式应用。** 新的恢复提示、托盘、提权接管也要保持这一语义。
2. 当前常规启动在 `main.py` 请求整进程 UAC；取消则只读。`--monitor-only` 与 `--smoke-test` 不请求提权。
3. 当前恢复原值仅保存在 `Scheduler.originals` 内存字典里，不跨应用会话保存；正常退出有取消、保留、恢复三种选择。崩溃后仍能恢复是待做能力。
4. 实例通过 PID、创建时间、名称识别；写入前核验句柄身份。读取原值失败的字段不写入，写入后必须回读，部分失败如实显示。
5. 停止规则只停止后续应用，不恢复当前值；恢复原设置与“默认”预设也不同。新增暂停/继续功能必须分别定义这些行为。
6. UI 通过信号向工作线程提交意图；GUI 不直接执行进程调度调用。保留 pending 命令、恢复回执和异步退出的顺序保障。
7. 使用正常 Windows 用户态接口；不添加终止/挂起目标进程、服务/驱动管理、注入、绕过保护或 Realtime 优先级。
8. 真实调度写入测试仅对测试自己创建并明确限定 PID 的协作子进程进行；不能因为发现真实 ACE 进程就自动用它测试。
9. 不从 CPU 编号猜测 P/E 核，不把 Affinity 百分比当 CPU 使用率硬上限，不把进程 I/O 当物理磁盘读取。目前多 Processor Group / 超过 64 个逻辑 CPU 时 Affinity 会降级禁用。

## 4. 建议实施顺序与验收

下一阶段可暂称“日常可用性版本”，是否使用 v1.5.0 在完成范围明确后再决定；不要现在移动 v1.4.0 标签。

### A. 恢复记录与应用生命周期

先梳理“仅观察、已应用、等待操作、等待恢复、隐藏到托盘、真正退出、异常结束”等状态，再接入新界面入口。

- 设计带 schema 版本的恢复记录，保留原值、最近写入值、实例身份和所属会话；明确跨系统重启及过期记录处理方式。
- 写入目标进程前先可靠保存恢复所需数据；记录保存失败则阻止相应新修改。保留逐字段部分失败语义，避免写进程成功却没有可追溯记录。
- 下次启动仅提示存在未完成会话，不自动套用旧策略；用户请求恢复时重新核验同一实例。
- 如果当前值已被其他程序改过，不应静默覆盖；明确显示冲突和可选择的处理方式。已退出或身份变化的实例不能恢复。
- 恢复成功后清理对应字段，失败保留可重试记录；用户明确选择保留并退出时，应区分这是已完成的用户决定，避免下次误报为崩溃。
- 这是“重新打开后可以恢复”，不承诺应用崩溃瞬间自动恢复。独立守护进程不是当前阶段的默认实现。

验收：故障注入后的再次启动、记录写失败、损坏/旧版记录、PID 重用、目标退出、外部修改冲突、部分恢复失败与重试、重复点击退出，均有明确结果；真实写入仅使用自建进程。

### B. 托盘、再次启动与后台行为

- 托盘显示观察/已应用/错误状态，提供打开窗口、停止或暂停维护、恢复原设置、真正退出。
- 在偏好中明确关闭按钮是退出还是隐藏；首次行为应清楚可见。真正退出继续经过 A 的恢复与等待流程。
- 第二次启动应唤醒已有实例；当前实现只弹锁定提示，需要加入可验证的本机实例通信，保留单实例保障。
- 处理托盘不可用、Explorer 重启、睡眠唤醒；后台隐藏可降低绘图频率，采样和维护定时器分别管理。
- 开机启动属于后续可选设置，不能默认开启；启动仍只观察。

验收：隐藏后可找回、不会出现两个维护实例、操作中退出不会抢先结束、恢复失败不静默退出、托盘不可用有可操作退路。

### C. 按需提权

- 普通启动可以查看指标、编辑规则和保存草稿；需要调度写入时才请求 UAC。
- 第一阶段可评估完整应用按需提权接管；必须设计单实例锁、未保存草稿、恢复记录和正在执行操作的交接。
- 用户取消提权时保留窗口与编辑状态；提权成功后仍不能隐式应用未确认的其他规则。
- 后续如拆分独立执行进程，只允许受限的调度命令，校验本机调用者、消息和进程身份，不做任意命令执行通道。

验收：普通用户/管理员启动、取消 UAC、接管失败、同时启动、已有恢复记录均可解释；安全桌面交互若无法自动测试要如实标注。

### D. 诊断导出与关于页面

- 增加统一异常处理与工作线程失败提示，避免工作线程停了但界面仍显示正在维护。
- 一键导出诊断 ZIP：程序版本/构建号、Windows 版本、拓扑摘要、必要日志、脱敏配置摘要；导出前说明内容，不默认联网上传。
- 关于页面集中提供版本、更新入口、反馈入口、配置目录、第三方许可。可先打开受访问权限控制的 GitHub Releases，不伪装成已有自动更新器。
- 保留现有轮转和内存日志上限，错误反馈不重复刷屏。

验收：导出取消、不可写目录、敏感路径脱敏、工作线程失败、离线打开关于页面、诊断中版本与 EXE 属性一致。

### 后续阶段

安装版、升级迁移与回退、可信签名、完整更新器；然后扩展配置方案/导入导出、深浅色与高对比度、完整可访问性、多显示器 DPI、长时间资源稳定性、可靠的 CPU 效率等级识别。

私有仓库的更新授权要单独设计，不能把维护者 GitHub Token 放进客户端。签名服务需要选择适用的主体与服务，不能把签名等同于保证免除 SmartScreen 提示。

## 5. 代码定位

| 文件 | 接手时重点 |
| --- | --- |
| `ace_scheduler/main.py` | 启动、UAC、QLockFile、日志、只读 smoke test |
| `ace_scheduler/ui/main_window.py` | 工作线程、待处理命令、恢复回执、关闭和收尾流程 |
| `ace_scheduler/core/scheduler.py` | `originals`、逐字段读写/回读、恢复与部分失败 |
| `ace_scheduler/core/process_monitor.py` | 规则激活/停止、采样/维护、实例变化、工作线程回执 |
| `ace_scheduler/core/process_metrics.py` | `ProcessIdentity` 与采样身份 |
| `ace_scheduler/windows/process_api.py` | 句柄权限和身份校验；不可绕过 |
| `ace_scheduler/windows/elevation.py` | 当前 ShellExecuteW runas 重启方式 |
| `ace_scheduler/config/` | schema v1、验证、原子保存、旧配置兼容 |
| `ace_scheduler/ui/pages/settings.py` | 托盘偏好、诊断/关于等入口的候选位置 |
| `tests/test_interactive_flows.py` | 退出、部分失败、排队操作和模态框回归 |
| `tests/test_interactive_windows.py`、`tests/test_windows_api.py` | 自建进程与真实 QThread / Win32 验证 |
| `tools/package_release.py`、`ace_scheduler.spec` | EXE 验收、打包、资源与许可证收集 |

## 6. 接手第一步与验证命令

1. 读取本文、[贡献规范](../CONTRIBUTING.md)、[架构](ARCHITECTURE.md)、[测试策略](TESTING.md)；检查 Git 状态和当前源码，不重做已完成的品牌/仓库工作。
2. 记录已有交接文档改动，创建 `codex/` 开头的功能分支；按仓库流程把首个功能的问题、范围和验收写清，再实施 A。避免一次将所有后续方向混成大改动。
3. 运行当前回归作为接手基线。新增测试以用户可观察行为、恢复正确性与故障语义为主；交接文档本身不需要重跑全部应用测试。
4. 每个阶段完成代码、适当自动测试和相关桌面验证再进入下一个阶段；最终按 [发布指南](RELEASING.md) 处理版本、CI 和发行包。

```powershell
git status --short
git log -3 --oneline
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ace_scheduler --smoke-test artifacts\handoff-source-smoke
.\.venv\Scripts\python.exe -m tools.preview_ui --output artifacts\handoff-preview
```

真实窗口检查和缩放命令见 [完整交互测试记录](INTERACTIVE_TEST_PLAN.md)。100%/150% 已验证范围不等于物理多屏 DPI 热切换。测试证据放 `artifacts/`，不上传个人配置或真实进程日志。

构建继续使用固定依赖和现有 spec。保留 spec 内对 DLL 搜索 PATH 的限制，曾经出现无关软件的同名 ICU DLL 被错误打包的问题。不要覆盖正在运行的 EXE 目录；使用新输出目录或先正常关闭本应用。

## 7. 新会话可直接使用的任务说明

> 继续推进 ACE Scheduler 的 Windows App 成熟化。先阅读 docs/HANDOFF.md、docs/ROADMAP.md 和 CONTRIBUTING.md，核对当前代码与 Git 状态。保留已有界面、品牌、批量策略及启动只观察等约束，从恢复记录和应用生命周期开始，再依次完成托盘、按需提权、诊断导出和关于页面。直接实施并完成相应验证，按阶段提交可审查结果；不要重做 v1.4.0 已完成的工作。仓库保持私有，调度写入测试只操作自建测试进程。
