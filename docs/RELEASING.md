# 维护与发布

## 日常维护

- 默认分支 `main`，通过 PR 和 squash merge 保持变更集中。
- CI 必须通过，讨论必须解决；合并后删除临时分支。
- 分支保护的可用性受 GitHub 账号方案影响；以仓库 Settings 中实际启用状态为准，不用文档代替强制保护。
- Dependabot 每周检查 Python 依赖和 Actions；依赖更新需通过相同测试，不启用无人审核自动合并。
- 使用 `.github/CODEOWNERS` 和标签区分界面、调度、配置、文档与构建问题。

## 版本准备

1. 更新 `ace_scheduler/__init__.py`、`assets/brand/version_info.txt`、README 和 CHANGELOG。
2. 在 Windows 完成自动回归和相关桌面检查，记录限制。
3. PR 合并到 main，确认该提交的 Windows CI 通过。
4. 在同一提交创建 `vX.Y.Z` 标签并推送。版本验证脚本会拒绝标签与代码不一致。

```powershell
git tag -a vX.Y.Z -m "ACE Scheduler vX.Y.Z"
git push origin vX.Y.Z
```

## 草稿发布

在 Actions 选择 **Release**，手动运行并输入已存在的标签。工作流检出该标签，验证版本，执行测试、构建和只读验收，随后创建 **Draft Release**，附带 ZIP 和 SHA-256。发布任务是唯一需要 `contents: write` 的任务，普通 CI 仅有读取权限。

维护者检查说明、压缩包结构、校验值和实际启动结果后，再发布草稿。私有仓库的 Release 仍受仓库访问权限控制。不要将 CI artifacts 链接当作永久版本下载地址。

## 产物

```text
ACE-Scheduler-vX.Y.Z-x64.zip
ACE-Scheduler-vX.Y.Z-x64.zip.sha256
```

ZIP 内为 `ACE-Scheduler/ACE-Scheduler.exe` 和完整 `_internal/`。包内含使用说明、第三方声明、运行依赖元数据、Python 许可证及版本清单；不得只分发 EXE。

```powershell
Get-FileHash .\ACE-Scheduler-vX.Y.Z-x64.zip -Algorithm SHA256
```

此校验用于检查文件完整性，不代表代码签名或独立供应链证明。目前没有 Windows 签名证书，不能宣称产物已签名。

## 回退

正常退出当前版本后解压之前的已验证版本。配置仍在同一用户目录；跨版本回退前自行备份配置。不要对正在运行的程序目录进行覆盖或强制删除。原始进程调度快照不跨会话保存，关闭前先处理恢复。
