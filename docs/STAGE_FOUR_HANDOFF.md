# 第四阶段交接：在游戏设备上继续效果验证

交接日期：2026-09-28。目标设备是用户的另一台 Windows 游戏电脑，**不是此前开发和验证的本机**。本文是本轮接手入口；`HANDOFF.md` 正文保留的是早期历史，不应照其旧任务说明重做已有功能。

## 当前事实与用户约定

- 已完成第一阶段可靠性补丁、第二阶段字段级策略、第三阶段实验会话；第四阶段尚未实现。第五阶段也未完成。
- 用户最新指示为“前三步打 tag 发布 release”，取代先完成所有阶段再发布的安排。第一至第三阶段合并为 v1.6.0；第四、第五阶段不包含在本次发行中。
- 优先以正式 `v1.6.0` 标签为接手基线，核对其源码、CHANGELOG 和发行包构建信息。发布流程尚未完成时不要将旧的 main 或 v1.5.1 当成新基线。
- 早先离线交接包保存的是发布前快照：HEAD 为 `2da511c8a05b91d79ac37652d6999de422ad0a60`，分支为 `codex/reliability-stage-one`，版本仍为 1.5.1，含前三阶段未提交改动。该包仅作为历史备份，不要覆盖 v1.6.0 源码，也不要把其中“不打 tag”的旧指示当作本轮发布限制。
- 本机第三阶段回归为 **249 passed in 32.87s**。真实调度测试仅操作自建子进程；未在真实 ACE 或游戏中验证性能收益。
- 第三阶段 EXE 已通过只读启动、五页面、图标、内嵌构建来源及诊断来源一致性检查；未提交构建被正式发布校验正确拒绝。1280×860、1040×700 的亮暗界面已检查。这些结果不能替代目标电脑验收。

## 迁移包与接手步骤

正式 Release 发布后，在游戏电脑优先克隆并从标签创建第四阶段分支：

```powershell
git clone https://github.com/chenlaoc/ace-scheduler.git ace-scheduler-stage4
Set-Location ace-scheduler-stage4
git switch -c codex/effect-validation v1.6.0
```

随后按下方命令安装依赖、验证并只读启动；向新 Codex 会话说明：阅读本文，从 v1.6.0 继续第四阶段，先做 4.1/4.2 再做 4.3/4.4，前三阶段已单独发布，第四阶段不自动发布。

以下保留旧离线包恢复说明。迁移包为 `ACE-Scheduler-stage4-handoff-20260928.zip`，是发布前的开发交接备份，不是正式发行包。只有需要还原旧快照时才使用，解压到新的普通目录，不覆盖现有仓库。

| 内容 | 用途 |
| --- | --- |
| `source/` | 完整的当前源码、资源、文档和测试，包括未提交与未跟踪文件 |
| `repository.bundle` | 离线 Git 基线；无需访问 GitHub 即可恢复仓库历史 |
| `tracked-changes.patch` | 已跟踪文件相对 HEAD 的二进制兼容补丁，仅供核查；不包含未跟踪文件，不能单独代替 source |
| `Restore-Workspace.ps1` | 在全新目标目录克隆离线基线并叠加 source；不创建提交、不运行程序、不修改系统设置 |
| `binary/ACE-Scheduler/` | 第三阶段测试程序，EXE 与 `_internal` 必须一起保留 |
| `evidence/` | 原机启动验收摘要、界面预览及验证说明 |
| `NEXT_SESSION_PROMPT.txt` | 可直接发送给目标设备 Codex 的接手任务 |
| `MANIFEST.json` | 包内各文件 SHA-256、大小及来源；完整压缩包校验值在旁边的 `.sha256` 文件 |

建议安装 Git 和 64 位 Python 3.13，在 PowerShell 中从解压目录执行。若目标目录已存在，脚本会拒绝覆盖，请换一个新目录。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Restore-Workspace.ps1 -Destination D:\Dev\ace-scheduler-stage4
Set-Location D:\Dev\ace-scheduler-stage4
git status --short
git log -1 --oneline
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ace_scheduler --smoke-test artifacts\game-pc-baseline-smoke
.\.venv\Scripts\python.exe -m ace_scheduler --monitor-only
```

`ExecutionPolicy Bypass` 仅用于这一次 PowerShell 进程，不要求更改系统策略。依赖安装需要网络或另备对应 wheel；迁移包不携带 Python 或原机虚拟环境。测试会操作自己创建的子进程，不会自动对发现的游戏/ACE 应用策略。第三阶段测试数仅作基线，目标设备的失败和跳过均须记录原因，不能忽略后继续声称全通过。

原机 `%APPDATA%\ACE-CPU-Scheduler` 的配置、实例锁、恢复日志和个人日志不在包内；这些属于原机运行状态。新电脑使用自己的状态目录，已有配置先备份。第三阶段实验会话仍需手动保存 JSON，打开旧实验只读，不触发调度。

还原保留源码原始字节；Git 的换行转换可能使少数无内容差异的文件也显示为 modified，以 `git diff` 核对，勿因此丢弃其他改动。迁移验收会比较源码逐文件哈希、完整 Git diff 和未跟踪文件列表。

## 第四阶段实施顺序

### 4.1 磁盘记录

先在目标电脑探测 Windows PDH 的 PhysicalDisk 实例与可用计数器，记录磁盘编号、可识别名称、盘符映射及其不确定性，再实现可选磁盘采集。优先用 `PdhAddEnglishCounterW` 避免依赖系统语言；检查每次取样状态，不能只凭添加计数器成功认定数据有效。

记录读写吞吐、IOPS、平均读写延迟和队列指标，明确每项单位及平均/瞬时语义。延迟跨区间汇总需按请求数量加权；一秒平均延迟的 P95 不能标成单次 I/O 延迟 P95。无请求时延迟应与有效零延迟区分。设备总体指标不得归因成某个进程专属负载。

采集与调度、恢复解耦，不阻塞 GUI 或维护定时器；一次采集可供多个实验引用，避免按实例重复查询磁盘。计数器缺失、设备移除、重置、休眠恢复及采样超时保留缺口，不补零、不连接失效身份。磁盘枚举更新后重新校验选择。

### 4.2 场景和时钟

给会话增加场景名称、备注、进入游戏/加载/对局/卡顿等时间标记；支持保存、重开及完整导出。场景标记本身不能触发策略应用。

第三阶段使用单调时钟加固定 UTC 偏移，只有近似对齐能力。扩展会话 schema，保存单调时钟、QPC（如采用）的频率和锚点、UTC 配对以及对齐来源/误差；记录时钟跳变与休眠断点。支持读取第三阶段 schema 1，不伪造旧数据不存在的精确时间。

### 4.3 外部帧时间导入

首版优先导入明确版本/表头的 PresentMon CSV。先用测试夹具开发解析器，在游戏设备上取得代表性导出后核对。记录工具版本、文件哈希、字段/单位映射、选择的进程和交换链/数据流、帧口径以及对齐偏移。CPU 提交间隔与显示帧间隔分别命名，不混为同一指标。

优先使用可核验的时间锚点；仅有相对时间时提供手动校准，明确未对齐/近似对齐。只统计窗口交集，分别显示覆盖率。未知格式、损坏/乱序/重复时间戳、多数据流、无交集和大文件都应有明确结果。先交付中位数、P95/P99 和按用户阈值统计的长帧，写清统计口径；不凭文件名猜采集时间。

### 4.4 重复实验汇总

按场景、硬件、应用/游戏版本、策略、磁盘和指标口径分组；每轮保存独立数据，展示配对差值、中位差异、离散程度和有效轮数。不同帧率的实验不能直接把所有帧拼起来冒充独立重复。失败、中断或覆盖率不达标的轮次默认排除，并显示原因；阈值需可见、可解释。

实测建议先做至少三组相似场景的配对观察，交替安排 A/B，记录加载、缓存状态和后台活动；这只是起始观察量，不构成统计显著性保证。恢复原设置与选择“默认”预设不同。报告分别呈现进程 I/O、设备负载和帧时间，保留不一致与无改善的结果。

## 目标设备验收记录

证据放本机 `artifacts/stage-four/`，个人路径、实际进程日志和游戏记录不直接提交仓库。开始前记录：

- Windows 版本/构建、CPU、处理器组、可用逻辑 CPU、GPU/驱动、内存、目标磁盘及游戏所在卷。
- 游戏名称/版本、画质/分辨率/限帧、固定场景、PresentMon 版本与 CSV 表头。
- 代码基线与工作区状态、Python/依赖版本、应用构建信息；各项自动测试的通过/失败/跳过。

| 验收 | 需要保存的证据 |
| --- | --- |
| 原功能未回退 | 当前完整回归、只读启动、字段级应用与恢复的自建子进程验证 |
| 磁盘采样正确 | 本机计数器探测、与系统性能监视器同口径对照、自建文件负载、缺口和设备变化测试 |
| 时间对齐正确 | 已知偏移夹具、边界/无重叠/休眠测试、真实 CSV 的时间轴核对 |
| 保存和复查可靠 | schema 1 兼容、新 schema 往返、退出实例/重开程序后报告一致、导出失败保留原文件 |
| 工具自身开销 | 同等负载下开/关新增采集的扫描耗时、CPU、内存、句柄与记录量，含隐藏窗口和长时运行 |
| 游戏效果 | 每轮原始会话及帧记录、场景和操作、实际回读、恢复结果、排除原因及跨轮汇总 |

开发自动测试继续只对自建进程写入。真实游戏/ACE 的策略由用户在实验时明确选择并应用，不能把本次“准备交接”当成后台自动修改游戏进程的授权。功能验收与游戏收益结论分别报告。

## 代码入口和约束

| 文件 | 当前职责 / 第四阶段接点 |
| --- | --- |
| `core/experiment.py` | 独立会话、固定 before/transition/after、原始观察、追加事件、统计、schema 1 JSON/CSV |
| `core/process_monitor.py` | 采样先于操作；apply/maintenance/restore 结构化事件；计数器重置与生命周期中断 |
| `core/process_metrics.py` | 进程计数器差分；缺失和首次采样不当作零 |
| `core/scheduler.py`、`core/recovery.py` | 逐字段原值/请求值/回读、pending 暂停、写前持久化与恢复冲突 |
| `core/policy.py`、`config/models.py` | 共享策略解析/预览，schema 2、字段不修改与 EcoQoS 四态 |
| `ui/pages/experiment.py`、`ui/main_window.py` | 会话选择、已退出实例、保存/导入、事件和比较展示 |
| `windows/file_info.py` | 按实例缓存目标文件版本信息 |
| `tests/test_experiment.py`、`test_monitor.py`、`test_interactive_flows.py`、`test_windows_api.py` | 固定窗口、事件顺序、退出后导出、真实 Windows 回读测试 |
| `build_metadata.py`、`tools/package_release.py`、`ace_scheduler.spec` | 构建来源与正式发布校验；本地脏测试包不得伪装为发行包 |

以上代码路径均相对 `ace_scheduler/`，`tests/`、`tools/` 和 spec 除外。保留启动仅观察、显式应用、实例身份核验、逐字段恢复、pending 阻止重写、部分成功和缺失值语义。实验导入不得激活规则。第三阶段最多保留 64 个会话，每会话 4096 样本/1000 事件；第四阶段的磁盘/逐帧数据要另做有界存储，不能简单塞进现有内存列表或沿用 8 MB 导入上限而不评估。

优先从 v1.6.0 创建 `codex/effect-validation` 分支。若已经在旧离线快照上开始第四阶段，先保存工作区并核对差异，再移植增量到正式基线，不能直接覆盖或重置。后续提交/推送按目标会话授权与仓库流程处理；本轮 v1.6.0 发布授权不等于授权提前发布第四阶段。

测试版构建用独立目录，不直接运行要求干净发布来源的发行打包流程：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath artifacts\stage-four-dist --workpath artifacts\stage-four-build ace_scheduler.spec
.\artifacts\stage-four-dist\ACE-Scheduler\ACE-Scheduler.exe --smoke-test artifacts\stage-four-packaged-smoke
```

参考：[Microsoft PDH](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhaddenglishcounterw)、[PresentMon CSV/时间列说明](https://github.com/GameTechDev/PresentMon/blob/main/README-ConsoleApplication.md)。接入时按实际版本核对；第四阶段先完成 4.1/4.2，再推进 4.3/4.4。
