# 维护与发布

## 日常维护

- 默认分支 `main`，通过 PR 和 squash merge 保持变更集中。
- CI 必须通过，讨论必须解决；合并后删除临时分支。
- `main` 要求 PR、最新基线上的 CI、线性历史与讨论解决，管理员也需遵守。以仓库 Settings 中实际启用状态为准。
- Dependabot 每周检查 Python 依赖和 Actions；依赖更新需通过相同测试，不启用无人审核自动合并。
- 使用 `.github/CODEOWNERS` 和标签区分界面、调度、配置、文档与构建问题。

### 公开维护配置

默认分支为 `main`；启用 Issues、Squash 合并、合并后删除分支、Dependabot 漏洞提醒与安全更新，关闭 Wiki 和 Projects。Actions 默认使用只读令牌，不能代替维护者批准 PR。外部贡献者的工作流均需维护者批准后运行，发布工作流只允许手动触发。

仓库公开后启用私密漏洞报告、Secret scanning 和 Push protection。`main` 禁止直接推送、强制推送与删除，保护规则适用于管理员。合并 PR 前必须解决讨论、更新到最新基线，并通过以下由 GitHub Actions 提供的检查：

- `Tests (Python 3.11)`
- `Tests (Python 3.13)`
- `Package (Windows x64)`

目前只有一名维护者，批准人数设为 0，不要求作者无法给自己的 PR 提供的第二人批准。已有审核在新提交后失效。增加维护者后可将批准人数调整为 1，并启用 Code owner 审核。

2026-09-28，维护者决定公开仓库，并确认保留现有提交历史及作者元数据。公开前检查 Git 历史、Issue/PR、工作流和 v1.5.0 发布附件。许可证仍保留全部权利，公开可见不代表采用了开源许可证。历史文档中的“保持私有”只记录当时的决定，不再作为当前约束。

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

维护者检查说明、压缩包结构、校验值和实际启动结果后，再发布草稿。公开仓库的正式 Release 可直接下载；草稿只供维护者检查。不要将 CI artifacts 链接当作永久版本下载地址。

v1.5.0 在仓库公开前构建，包内“关于”页和文档仍可能提到私有访问权限。下载权限以 GitHub 当前设置为准；不替换已发布 ZIP 或移动标签来修改这段历史文案。

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

正常退出当前版本后解压之前的已验证版本。配置仍在同一用户目录；跨版本回退前自行备份配置。不要对正在运行的程序目录进行覆盖或强制删除。v1.5.0 的恢复记录跨应用会话保存；回退旧版前先处理恢复并备份配置目录，旧版不会识别新的恢复记录。
