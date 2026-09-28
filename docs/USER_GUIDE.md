# ACE Scheduler 使用指南

## 一次实验的流程

1. 启动工具和游戏，在“运行概览”的进程表或右上角选择实例，例如 SGuard64.exe。多个同名进程各占一行。
2. 保持观察约 60 秒，获取基线；未发现时，默认规则仍保留在“调度策略”页。
3. 打开“调度策略”，勾选目标名称，统一选择“较强 / 温和 / 默认”，或直接修改各行的参数。
4. 点击“应用勾选”。各行使用各自参数，覆盖该名称的所有当前实例；本会话中以后发现的新实例也会应用。只是选择预设或点击“保存勾选”不会修改目标进程的调度值。
5. 在“运行概览”查看真实回读的 Priority / Affinity / EcoQoS 和 Status，或到“设置与日志”查看操作结果。失败不会标成成功。应用前最多 60 秒的数据会冻结为 Before，后续最近最多 60 秒为 After。
6. 打开“实验对照”，在相近游戏阶段比较 CPU%、Read/Write MB/s 的时间加权均值和采样峰值，并按需导出所选实例的 CSV。
7. 在“调度策略”点击“恢复勾选规则”，或在“设置与日志”点击“恢复全部原设置”结束实验。恢复使用该实例在本会话第一次被修改前的实际值，包括 EcoQoS 的“系统管理”状态。

规则以 `.exe` 文件名精确、不区分大小写匹配；不按路径认证程序身份。添加自定义规则时请确认目标名称，同名可执行文件都会匹配。

“停止自动应用”、禁用规则、删除自定义规则会停止后续管理，**已经设置的调度值会保留**。原状态仍可通过“恢复全部原设置”恢复。关闭窗口时，如果仍有可恢复实例，会提供恢复后退出、保留后退出、取消三个选项；恢复失败会保留窗口并显示日志。强制结束本工具、崩溃或重启后，原状态快照不会跨会话保留。Default 是固定基线预设，不等同于恢复先前值。

## 调度预设与动态 CPU 选择

| 预设 | Priority | Affinity | EcoQoS |
| --- | --- | --- | --- |
| Default | Normal | 全部可用逻辑 CPU | OFF |
| Mild | Below Normal | 最后 25%，向上取整，至少 2 个，不超过可用总数 | ON |
| Strong | Idle | 最后 1 个可用逻辑 CPU | ON |
| Custom | 用户选择 | 用户选择 | 用户选择 |

Priority 提供 Idle、Below Normal、Normal、Above Normal、High；不提供 Realtime。提高优先级可能增加与其他程序的竞争，实验通常从较温和的配置开始。

启动时通过 `psutil.cpu_count(logical=True/False)` 获取核心数，并通过 Windows 活跃 Processor Group 数量、总活跃处理器数、系统 Affinity mask 检查逻辑 CPU ID。不会从“物理核心 × 2”推导线程数，也不会把本工具自身可能已经受限的 Affinity 当作整个系统的 CPU 列表。物理核心数取不到时显示“未知”。

预设从当前可用 ID 的有序列表计算，支持不连续 ID；“最后 1 个/2 个”仅指逻辑处理器编号，不代表 P-Core 或 E-Core。没有根据编号猜测核心类型。为后续 `GetSystemCpuSetInformation` / `EfficiencyClass` 拓扑扩展保留了独立模块。

25%/50% 表示选取的逻辑处理器数量比例，**不是 CPU 用量百分比硬上限**。例如 16T 的 Strong 选择 `[15]`，24T 选择 `[23]`，无需更改源码或配置。单逻辑 CPU 机器的 Mild 自动降为一个 CPU。

系统存在多个 Processor Group，或逻辑处理器数超过 64 时，显示“当前版本仅支持单 Processor Group”，禁用 Affinity 编辑和写入；Priority、EcoQoS 与监控继续工作，日志明确标注跳过 Affinity。不会把跨组编号拼成错误掩码。64T 单组通过无符号指针宽度掩码支持第 63 位。

## Apply Once 与 Keep Enforced

- Apply Once：对每个 `PID + create_time + name` 实例，每次点击应用只尝试一次。进程重启后出现的新实例重新应用。
- Keep Enforced：周期性回读并仅在不符合策略时写入，包括 EcoQoS。采样默认 1 秒，维护默认 3 秒；两个后台计时器独立，支持 1/2/3/5/10 秒。
- 权限不足、API 不支持或部分失败时明确显示原因。Keep Enforced 失败后至少等待 30 秒再尝试，避免高频反复调用；也可手动点击应用立即重试。
- 修改并保存已生效规则会停止该规则的自动应用，点击应用后才启用新策略。每次重启工具都回到仅监控状态。

## EcoQoS 的含义

使用 `Get/SetProcessInformation(ProcessPowerThrottling)`，只控制 `PROCESS_POWER_THROTTLING_EXECUTION_SPEED`。ON 设置 ControlMask 和 StateMask 对应位；OFF 保留控制位并清除状态位；恢复时按原始状态恢复该位，同时保留无关的节流位。

界面显示 ON、OFF、“系统管理”或“未知”。“系统管理”不等于 OFF；未知/不支持也不会被当成关闭。EcoQoS 是 Windows 的节能调度提示，Windows 11 之前对应 LowQoS，既不承诺固定频率，也不保证任务管理器的“效率模式”标识完全等同于这个单独设置。[微软文档](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation)

## 监控指标与实验限制

CPU% 根据进程 user+system CPU 时间差，除以真实经过的单调时钟时间和系统逻辑处理器数，归一化到整机 0–100%。一个逻辑 CPU 满载在 N 个逻辑 CPU 的机器上约为 100/N%，与 psutil 原生可能超过 100% 的进程 CPU% 口径不同。RAM 是工作集，MB 为十进制 1,000,000 字节。

Read/Write MB/s = 两次 `psutil.Process.io_counters()` 字节差 / 实际秒数 / 1,000,000。Total Read/Write GB 是进程自启动累计值 / 1,000,000,000，并非工具启动后累计。read_count / write_count 在采样和 CSV 中保留。首次采样、权限拒绝、计数器回退和跨应用边界的首个采样周期均不伪造速率，显示 `—`，曲线留空。峰值是采样区间平均速率的最大值，不能捕捉小于采样间隔的所有瞬时尖峰。

这里是 **Windows 进程级 I/O 计数器**，包含文件、网络和设备等 I/O；缓存、预读、其他进程、内核驱动归属都会影响它与物理磁盘活动的对应关系。不能将它称为 SSD 物理 NAND Read，也不能从这个数字推导 SSD 寿命变化。[psutil 说明](https://psutil.readthedocs.io/stable/#psutil.Process.io_counters)

CPU 调度限制不等于磁盘限速，I/O-bound、异步 I/O、缓存命中或内核完成的工作可能不受预期影响。进程优先级仅影响调度竞争，系统空闲时 Idle 仍可运行。工具不承诺降低读峰值、修复 SSD SMART 错误或改善游戏体验。ACE/游戏的受保护行为也可能拒绝修改或对延迟作出反应；不保证兼容或不会引起游戏报错。建议使用相似场景重复对照，记录操作是否实际成功；不要把不同扫描阶段的差异直接解释为因果关系。

历史保留最近 60 秒；应用前对照单独冻结。再次应用/恢复会重建该实例的对照，进程重启后使用新身份，旧实例不拼接。最多缓存最近 16 个已退出实例以限制内存；CSV 导出面向当前选中的运行实例，建议退出前导出。CSV 包含 before/after/observe、最近操作结果、样本时长、进程创建时间和指标；采样 UTC 为导出时由单调时钟换算的近似值。空单元格表示缺失数据，不表示零。未实现物理磁盘 ETW 追踪或持续落盘采样。

## 配置与日志

`%APPDATA%\ACE-CPU-Scheduler\config.json` 保存规则、优先级、EcoQoS、维护模式、采样/维护间隔、窗口位置。预设保存语义，例如：

```json
{
  "mode": "last_n",
  "count": 2,
  "percentage": 25,
  "minimum": 1,
  "cpus": []
}
```

只有 Custom 保存具体 ID；换机器后过滤不存在的 ID，若一个也不剩，明确阻止应用并要求重新选择，不会写入零掩码。配置通过临时文件和原子替换写入。损坏/不兼容配置备份为 `config.invalid-*.json`，本次使用默认监控配置并在界面提示。

日志位于同目录的 `scheduler.log`，按 512 KB 轮转，保留两份旧文件；只记录发现、退出、操作、错误等事件，不持续写每秒指标。GUI 日志最多 500 行。日志包含进程名、PID、最终解析的 CPU ID、各项操作结果。每用户使用配置目录锁避免多个实例同时维护策略。
