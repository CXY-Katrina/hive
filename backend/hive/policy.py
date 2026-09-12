from datetime import timedelta


def evaluate_lease(request, devices, samples, stamp, period=15):
    """Pure decision. No PID heuristic overrides the confirmed ten-minute policy."""
    if request["purpose"] != "debug" or request["status"] != "ACTIVE":
        return "keep", "不是活动调试申请", {}
    if not request.get("protected_until") or stamp < request["protected_until"]:
        return "keep", "30 分钟保护期内", {}
    start = stamp - timedelta(minutes=10)
    evidence = {"start": str(start), "end": str(stamp), "devices": {}}
    if not devices:
        return "unknown", "缺少分配设备", evidence
    for device in devices:
        if device["quality"] != "ok" or device["health"] != "OK":
            return "unknown", "设备异常或缺测", evidence
        rows = samples.get(device["id"], [])
        rows = sorted(rows, key=lambda r: r["sampled_at"])
        # Require a boundary sample and every expected interval; no filling outages.
        before = [r for r in rows if r["sampled_at"] <= start]
        after = [r for r in rows if start < r["sampled_at"] <= stamp]
        window = before[-1:] + after
        if not window or window[0]["sampled_at"] > start or (start - window[0]["sampled_at"]).total_seconds() > period * 1.5:
            return "unknown", "10 分钟起点缺测", evidence
        if (stamp - window[-1]["sampled_at"]).total_seconds() > period * 1.5:
            return "unknown", "最新采样缺失", evidence
        if any((b["sampled_at"] - a["sampled_at"]).total_seconds() > period * 1.5 for a, b in zip(window, window[1:])):
            return "unknown", "10 分钟窗口存在缺测", evidence
        if any(r["quality"] != "ok" or r["ai_core"] is None or r["boot_id"] != device["boot_id"] for r in window):
            return "unknown", "窗口数据无效或节点重启", evidence
        if any(r["ai_core"] > 0 for r in window):
            return "keep", "申请卡最近 10 分钟有计算活动", evidence
        if any(r["ai_core"] != 0 for r in window):
            return "unknown", "无效利用率", evidence
        evidence["devices"][device["id"]] = {"samples": len(window), "all_zero": True}
    return "cleanup", "所有申请卡连续 10 分钟 AI Core 全零", evidence
