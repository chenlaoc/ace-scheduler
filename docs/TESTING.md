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
