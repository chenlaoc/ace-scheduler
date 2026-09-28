# 开发指南

## 环境

Windows x64、64 位 Python 3.11+；本机验证使用 Python 3.13，CI 使用 3.11 / 3.13。GUI 为 PySide6-Essentials，监控为 psutil，调度使用 ctypes 调用正常 Win32 API。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m ace_scheduler --monitor-only
.\.venv\Scripts\python.exe -m pytest -q
```

`requirements.txt` 是运行依赖范围，`requirements-dev.txt` 增加测试与打包工具，`requirements-lock.txt` 是验证过的完整固定版本。CI 和发布使用固定版本；维护依赖时同时验证范围和锁定文件。

## 结构

```text
ace_scheduler/
  main.py                  启动、只读模式、UAC、实例锁和日志
  branding.py              产品名和离线 Logo 资源定位
  config/                  配置模型、输入验证、原子保存
  core/                    拓扑、采样、调度、工作线程、对照
  windows/                 Win32 最小权限句柄、EcoQoS、窗口框架
  ui/                      页面、组件、主题和窗口控件
assets/brand/              Logo、Windows 版本资源
tests/                     单元、Qt 交互与真实子进程集成测试
tools/                     界面预览、窗口验证、打包验收
.github/                   CI、依赖更新、模板和代码责任人
```

GUI 通过 queued signals 向工作线程传递意图。工作线程拥有 MonitorEngine 和 Scheduler；修改前验证原值与句柄身份，修改后读回。完整失败语义见 [架构](ARCHITECTURE.md)。

## 本地构建

```powershell
.\build.bat
```

顺序为安装固定依赖 → pytest → PyInstaller → 打包程序只读 smoke test → ZIP / SHA-256。输出为 `dist/ACE-Scheduler/` 与 `dist/ACE-Scheduler-vX.Y.Z-x64.zip`。

PyInstaller 使用 onedir、windowed 和无 UPX，避免每次启动解包。spec 只在构建进程中限定 DLL 搜索 PATH，防止其他程序的同名 ICU 等 DLL 被错误收集。PNG 图标由 PyInstaller 在构建时转换为 Windows 图标，Pillow 仅用于构建。

构建目录如果被正在运行的程序占用，请正常关闭该程序，或为 PyInstaller 指定独立 `--distpath`，不要强制结束其他用户进程。诊断包可在当前终端设置 `ACE_SCHEDULER_CONSOLE=1`，正常发布时不要设置。

## 界面与窗口检查

```powershell
.\.venv\Scripts\python.exe -m tools.preview_ui --output artifacts\preview
.\.venv\Scripts\python.exe -m tools.window_smoke --output artifacts\window
```

预览使用明确标注的合成数据；`docs/images/` 只放可随仓库分享的预览图，不上传实际用户日志或进程环境截图。更多操作见 [测试指南](TESTING.md)。
