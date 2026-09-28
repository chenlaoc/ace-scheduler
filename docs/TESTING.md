# 测试策略

## 三个层次

| 层次 | 验证内容 | 不代表什么 |
| --- | --- | --- |
| 单元与故障注入 | 拓扑、指标、PID 重用、权限拒绝、配置失败、部分恢复 | 不代表真实受保护进程允许写入 |
| Qt 交互 | 鼠标/键盘事件、实际模态框、批量编辑、CSV、异步退出 | 不等于所有操作都经过桌面坐标自动化 |
| Windows 实机 | 自建子进程调度读回、本应用 HWND、打包程序启动 | 不代表不同硬件/多屏/游戏场景已通过 |

## 快速回归

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=artifacts\tests.xml
.\.venv\Scripts\python.exe -m ace_scheduler --smoke-test artifacts\source-smoke
```

默认 Qt 测试使用 offscreen 平台，Windows API 测试仍调用真实系统接口。带 `windows` 标记的测试只操作自己创建的协作子进程，结束时恢复原值并经 stdin 请求正常退出。

```powershell
.\.venv\Scripts\python.exe -m pytest -m windows -q
.\.venv\Scripts\python.exe -m pytest tests/test_interactive_flows.py tests/test_interactive_windows.py -q
```

不要把真实 ACE 的 PID 加入写入测试。模拟截图必须带示例数据标记；首次采样、权限拒绝和未知值不得伪装成 0。

## 桌面回归清单

完整动作、预期、已发现问题与分层结果见 [交互测试计划](INTERACTIVE_TEST_PLAN.md)。发布前至少检查：

- 四页导航、默认尺寸与 1040×700 小窗口、CPU 编辑和操作栏可达。
- 全选/部分勾选、未选草稿保留、保存与应用的区别、失败回退。
- 按钮与键盘退出，正在执行时等待；取消/保留/恢复以及恢复失败重试。
- 只读模式、第二实例、旧配置重启、损坏配置备份。
- 无系统标题栏、拖动、最大化工作区、最小化与还原。

```powershell
$env:QT_QPA_PLATFORM = "windows"
.\.venv\Scripts\python.exe -m tools.window_smoke --output artifacts\window-100
$env:QT_SCALE_FACTOR = "1.5"
.\.venv\Scripts\python.exe -m tools.window_smoke --output artifacts\window-150
Remove-Item Env:QT_SCALE_FACTOR
```

150% 在这里是 Qt 缩放参数，不修改 Windows 设置。跨屏 DPI 热切换仍需物理显示器单独验证。

## CI 与证据

CI 保存 JUnit XML、打包验收 JSON、页面截图和 ZIP 校验文件为 Actions artifacts。仓库只保存脱敏摘要与合成界面截图；`artifacts/` 是本地证据目录，不进入 Git。较早验证文档中的本地证据路径用于原工作区追溯，首次克隆时不会包含这些文件。

103 项回归是 v1.3.1 的验证基线；后续以相应提交的 CI 结果为准。历史测量与测试边界见 [验证记录](VALIDATION.md)。

## v1.5.0 日常可用性回归

新增 `test_recovery.py`、`test_lifecycle.py`、`test_elevation.py` 和 `test_diagnostics.py`，覆盖恢复记录故障、真实双进程 IPC/提权交接协议、后台停止/重启、诊断脱敏和关于页。按需提权协议自动测试不会触发真实 UAC 安全桌面；取消/授权实际桌面流程、Explorer 重启和系统睡眠需人工验收。

```powershell
.\.venv\Scripts\python.exe -m tools.tray_smoke --output artifacts\daily-tray-native
.\.venv\Scripts\python.exe -m tools.preview_ui --output artifacts\daily-preview
```

托盘检查仅创建本应用窗口，没有监控工作线程或调度写入。打包验收同时校验诊断 ZIP、关于页面及源码的版本一致性。具体实现与边界见 [日常可用性实现记录](DAILY_USABILITY.md)。

### 启动权限与主题

普通启动已改为打开窗口前申请管理员权限，测试模拟启动、授权取消/失败与已有窗口激活，不触发真实 UAC。`test_theme.py` 验证三种主题、旧配置、即时切换、持久化失败回退及系统通知；通知使用 Qt 信号模拟，不修改 Windows 个性化设置。`tools.preview_ui --theme light` 和 `--theme dark` 可生成两种尺寸的五页预览。UAC 安全桌面的实际操作仍需人工验收。

### 第一阶段可靠性回归

- `test_recovery.py` 在确认保存时注入一次故障，随后恢复正常存储，验证无变化重试、重新加载日志、改用其他策略都不能掩盖 pending；显式恢复后方可重新应用。
- `test_monitor.py` 验证采样和独立维护两个入口暂停受影响实例，停止或重新启用规则不能绕过暂停，同规则的新实例仍能应用。
- `test_scheduler.py` 验证不支持的 Affinity 被跳过且整体不是完整成功，并区分无变化与全部失败。
- `test_config.py` 覆盖缺少 0～5 条内置规则时的容量边界和 parse/save/load 往返。
- `test_package_release.py` 验证同版本不同提交、脏构建、来源缺失、运行报告不一致及锁文件变化都会阻止打包，并检查 manifest 使用构建时的依赖信息。这里的 EXE 与启动结果为模拟数据，实际 PyInstaller 构建和只读启动仍由 CI 执行。

### 第二阶段字段级策略回归

- `test_field_policy.py` 对未接管字段的 getter/setter 设置调用陷阱，验证单字段应用仅保存和恢复该字段；覆盖全不修改、多 Processor Group、旧原值保留和无效策略。
- `test_config.py` 验证 v1 布尔值语义迁移、原文件备份、备份失败不覆盖，以及 v2 四种 EcoQoS 状态往返。
- `test_interactive_flows.py` 在 1280×860 和 1040×700 下通过实际鼠标／键盘事件选择“不修改”、四种 EcoQoS 状态、预览和保存，确认预览不应用。
- `test_monitor.py` 验证保存新策略先停止维护，重新应用后只维护仍受接管的字段，已有原值不丢失。
- `test_windows_api.py` 对自建子进程执行 EcoQoS 开启／显式关闭／系统管理和恢复，对 Priority/Affinity setter 设置调用陷阱；不操作 ACE 或游戏进程。

### 第三阶段实验会话回归

- `test_experiment.py` 覆盖 A/B/恢复、多次应用、部分失败、固定窗口、样本与窗口交集、缺失区间、基线核验、PID 重用、只读 JSON 往返和 CSV 的策略／拓扑／事件上下文；导出失败保留旧文件，容量上限不自动删除旧会话。
- `test_monitor.py` 验证真实引擎中的采样／操作顺序，独立维护入口的结构化事件和写入后的差分重置。
- `test_interactive_flows.py` 在两种窗口尺寸验证退出实例仍可保存、重新打开，导入不发出调度命令；托盘隐藏不丢记录，恢复不替换旧会话。
- `test_windows_api.py` 仅操作自建子进程，核对记录中的真实前后设置、目标文件版本和恢复事件。
- JSON 是手动保存的完整快照；没有跨崩溃自动保存实验数据的保证。恢复日志仍独立保持写前持久化。
