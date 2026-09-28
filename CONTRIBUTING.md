# 参与 ACE Scheduler

欢迎通过 Issue 反馈问题或提出建议。仓库公开可见，但尚未采用开源许可证；代码贡献请先与维护者确认授权与范围，并阅读 [使用指南](docs/USER_GUIDE.md) 和 [架构文档](docs/ARCHITECTURE.md)。

## 一次改动的流程

1. 功能与行为变更先提交 Issue，写明可复现问题、目标行为和范围。
2. 从 `main` 创建短分支，如 `fix/restore-error` 或 `feat/policy-search`。自动协作分支使用 `codex/` 前缀。
3. 保持改动集中；UI 不直接调用进程 API，调度操作留在工作线程。
4. 运行相关测试及完整 Windows 回归，填写 PR 模板中的验证结果。
5. 等待 CI 通过、分支跟上最新 `main`、讨论解决后，使用 squash merge 合并。`main` 的保护规则也适用于管理员，禁止直接推送、强制推送和删除。

目前由一名维护者管理，不强制要求另一位审核者批准；PR、CI 与讨论解决仍是合并条件。外部贡献者的 Actions 运行需由维护者批准，批准前应检查工作流和构建脚本。

## 不可破坏的行为

- 新会话只观察；保存配置不隐式应用。
- PID、创建时间、名称共同标识实例，写入前验证句柄身份。
- 读取原值失败的字段不得写入；写入必须回读，部分失败不可报告成全成功。
- 调度恢复按实例和字段进行；停止维护不能冒充恢复。
- 不添加结束/挂起进程、驱动/服务管理、注入、绕过保护或 Realtime 优先级。
- 不把进程 I/O 解释为物理磁盘读取，不把 CPU 编号当作 P/E 核分类。

## 验证与提交

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ace_scheduler --smoke-test artifacts\source-smoke
```

真实 API 测试只能操作测试自己创建且明确限定 PID 的临时子进程。GUI 改动同时检查 1280×860 和 1040×700；窗口框架改动补充 100% / 150% 缩放验证。新测试应验证外部行为、失败语义和实际数据，而非重复实现细节。

提交采用 Conventional Commits，例如 `fix(ui): roll back failed interval changes`。一次提交表达一个完整变化，不提交 `.venv`、构建目录、日志、私人配置、凭据或第三方程序 ZIP。

新增依赖同时维护 `requirements*.txt`、第三方声明与构建说明。更新版本需同步 Python 版本常量、Windows 版本资源及 CHANGELOG。格式遵循 `.editorconfig`，Python 使用四空格缩进。

发现漏洞或可能修改错误进程的问题，请按 [SECURITY.md](SECURITY.md) 处理，不在普通 Issue 中发布敏感日志或利用细节。讨论请聚焦可复现事实，尊重参与者，不公布他人的隐私数据。
