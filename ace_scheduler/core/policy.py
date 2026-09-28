"""Resolve requested fields without opening or modifying a process."""
from dataclasses import asdict

from ace_scheduler.config.models import ECO_MODES, PRIORITIES, Policy
from ace_scheduler.windows.eco_qos import EcoState


def resolve_targets(policy, topology):
    policy = Policy.parse(asdict(policy))
    targets = {}
    if policy.priority != "unchanged":
        targets["priority"] = PRIORITIES[policy.priority]
    if policy.affinity.mode != "unchanged" and topology.affinity_supported:
        targets["affinity"] = topology.resolve(policy.affinity)
    if policy.eco != "unchanged":
        targets["eco"] = {"system": EcoState(0, 0), "on": True, "off": False}[policy.eco]
    return targets


def preview_policy(policy, topology):
    targets = resolve_targets(policy, topology)
    priority = "不修改" if policy.priority == "unchanged" else policy.priority
    if policy.affinity.mode == "unchanged":
        affinity = "不修改"
    elif "affinity" not in targets:
        affinity = "不支持，应用时跳过：" + topology.limitation
    else:
        affinity = "CPU " + ", ".join(map(str, targets["affinity"]))
    return f"Priority：{priority}\nAffinity：{affinity}\nEcoQoS：{ECO_MODES[policy.eco]}"
