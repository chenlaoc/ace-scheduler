# 参考分析与实现决策

分析日期：2026-09-28。用户提供的 `ACE-KILLER-v1.1.3-x64.zip` 有 91 个条目，包含 `ACE-KILLER.exe`（17,688,064 字节）、资源和运行依赖，未发现 `.py` 源文件。只读取 ZIP 目录，没有运行或反编译参考 EXE。

因此分析公开仓库 `adorablenew/ace-killer` 的 main 分支，检查时 HEAD 为 `4fb31f197358b063f7b63df48bfb67a4cb16a2af`。公开 main **未证实与所提供 v1.1.3 二进制完全一致**。它仅作为调度方式参考；本项目依据 Windows 文档独立实现，没有搬入原项目模块。

## 参考项目

[`core/process_monitor.py`](https://github.com/adorablenew/ace-killer/blob/4fb31f197358b063f7b63df48bfb67a4cb16a2af/core/process_monitor.py) 中的调度路径设置 Idle，使用逻辑 CPU 数减一选择最后一个 CPU，再通过 ProcessPowerThrottling 启用执行速度节流。这个三项组合值得参考。

需要修正的设计：它使用全访问权限；部分 ctypes 入口没有完整的指针宽度签名；部分优先级路径没有显式关闭句柄；缓存按名称只保留一个进程；“最后一个逻辑 CPU”变量被称为 small_core，无法证明是效率核。新实现使用最小权限、明确签名、finally 关闭、多个实例和 PID/create_time 校验，且提供 OFF 与精确恢复。

[`utils/process_io_priority.py`](https://github.com/adorablenew/ace-killer/blob/4fb31f197358b063f7b63df48bfb67a4cb16a2af/utils/process_io_priority.py) 单独使用 NtSetInformationProcess 的 ProcessIoPriority 类，并带有后台自动管理。为保持本次“仅 CPU 调度”实验变量一致，本版不移植该行为，仅保留独立扩展协议。

参考项目中还混有结束进程、服务管理、游戏关联、通知和内存清理。这些与本项目目标无关，均未引入。

## Windows 机制

- [Priority class](https://learn.microsoft.com/en-us/windows/win32/procthread/scheduling-priorities) 是线程基础优先级的组成因素，不是 CPU 速率配额。
- [GetProcessAffinityMask](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getprocessaffinitymask) 的系统掩码给出当前组可用 CPU；进程掩码只是其子集。单组版本使用 DWORD_PTR，x64 上是 64 位。
- [Processor Groups](https://learn.microsoft.com/en-us/windows/win32/procthread/processor-groups) 使普通掩码不能表达任意跨组集合。本版检测并阻止所有多组 Affinity 操作，保留其余功能。
- [SetProcessInformation](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation) 对执行速度节流位的 ON/OFF 与系统管理有不同语义；[GetProcessInformation](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocessinformation) 用于回读。保留其他节流位，避免修改无关 timer resolution 策略。

## 数据流与失败语义

GUI → 保存/应用信号 → Worker → MonitorEngine → Scheduler → verified HANDLE → Windows API → 回读 → 快照信号 → GUI。

配置只表达意图；规则激活状态仅限当前运行会话；恢复记录持久化到同一用户配置目录。发现进程后，重新构造 psutil.Process，核对名称与创建时间；Windows 写入前又在实际打开的句柄上验证，避免查到 PID 后进程退出并被重用的竞态。

字段级策略由 `core/policy.py` 解析，GUI 预览和 Scheduler 共用目标解析。`unchanged` 字段不进入调度目标，不参与读原值、写入与新恢复记录；监控快照仍可读取所有字段。EcoQoS 的 `system` 映射为 `EcoState(0, 0)`，`on/off` 映射为底层显式布尔目标；API 继续保留无关位。全字段不接管不打开进程写入句柄。切换为不接管不会删除以前的恢复记录。

配置序列化使用 schema v2；v1 的 EcoQoS 布尔值仅在配置解析入口迁移为 `on/off`。v2 与直接策略解析拒绝布尔值和未知状态，避免字符串真值造成误开启。迁移读取不写文件、不激活规则，第一次保存前备份旧配置。恢复日志仍为独立的 schema v1，存储具体原值与最后确认值，不依赖策略模式。

应用前先验证全部输入，包括 Custom 非空。读取原值失败的字段不写入，避免制造无法恢复的未知状态。不同字段独立尝试，部分成功明确报告，不把三个 API 伪装成事务。每次成功写入都立刻回读，仍可能被目标进程在之后改回；Keep Enforced 才负责下一次检查。

原状态按实例和字段可靠落盘后才能写入，写后回读并标记确认；恢复只恢复工具触及的字段。未确认写入或当前值不再匹配最近确认值时默认报告冲突，不静默覆盖。退出/重启不跨实例恢复。停止管理不会自动覆盖目标当前值；用户可选择恢复。未经用户点击应用，新发现实例只被采样。

CPU/I/O 采样使用单调时钟和累计计数器。应用前后切断差分基线，避免把横跨策略变更的周期当成纯 After。前后平均按有效采样时长加权；读写缺失值互不影响其他可用指标。图表每个指标单独坐标轴，空值不连线，不把 CPU% 与 MB/s 混成一条比例轴。

`ExperimentSession` 和 `History` 是独立于 Qt 的记录模型。MonitorEngine 按时间顺序发出采样前设置、操作前后快照、逐字段结果和生命周期事件，Worker 通过 `recorded` 信号传给模型；旧的文本 `applied` 信号不再驱动真实实验。普通采样先记录，再执行调度；应用／恢复和发生写入的维护会切断指标差分，避免跨操作区间混入纯 After。独立维护定时器也走同一个结构化操作入口。

会话固定窗口的结束由采样和时钟 tick 推进，缺失采样不延长观察期。统计按区间交集加权，原始样本不因投影到窗口而改写；已完成的观察不接受新样本，但允许追加恢复等后续事件。再次应用创建新 ID，实例退出只回收滚动缓存，不删除会话。JSON/CSV 原子导出，JSON 导入做大小、数量、类型、时间、身份与版本校验，仅恢复只读数据，不进入配置或调度命令链路。界面隐藏只减少渲染，仍消费结构化事件。

## 应用内标题栏

主窗口由 Qt 绘制标题和三个窗口按钮，使用 [FramelessWindowHint](https://doc.qt.io/qt-6/qt.html#WindowType-enum) 去除默认装饰；在本工具自己的 HWND 上保留 Windows 的可调整大小和最小化/最大化样式。按照 [Microsoft 自定义窗口框架文档](https://learn.microsoft.com/en-us/windows/win32/dwm/customframe)，处理 WM_NCCALCSIZE 去掉原生标题栏，WM_NCHITTEST 为边缘和标题区返回原生命中类型，按钮和进程选择框返回客户区命中。

WM_GETMINMAXINFO 和最大化时的客户区使用当前显示器工作区，按当前缩放比例处理最小尺寸和边缘宽度。原生双击、拖动与缩放由系统处理；Qt 标题栏事件提供后备的 [startSystemMove](https://doc.qt.io/qt-6/qwindow.html#startSystemMove) 路径。DWM 圆角和阴影属于可选外观。所有窗口管理调用仅针对本工具的窗口句柄，与被监控进程的调度 API 分开。

## 调度扩展边界

`CpuTopology` 可以增加 group/local-index/CPU-set/效率等级映射；在可靠检测前不提供任何“效率核”预设。`SchedulingExtension` 可供未来显式启用的 I/O 优先级策略实现，但 v1 不注册扩展、不加载 ntdll 来设置 I/O 优先级。跨组支持、ETW 物理磁盘分析和长时间记录不属于本版实现。

## 应用生命周期与诊断

托盘、恢复记录、同用户单实例 IPC、完整应用提权接管和异常停止的时序与验收见 [日常可用性实现记录](DAILY_USABILITY.md)。新进程只观察；提权文件只能传递经配置 schema 验证的编辑状态，不能携带自动执行命令。诊断从白名单字段构造 ZIP，任意日志正文不会原样导出。
