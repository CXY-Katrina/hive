from dataclasses import dataclass, asdict
import math
from .domain import DomainError


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    reducer: str = "time_weighted"
    scope: str = "device"
    version: int = 1
    minimum: float | None = None
    maximum: float | None = None
    hardware: str = "ascend"
    display: str = "line"


class MetricCatalog:
    def __init__(self):
        self._items = {}
        self.register(Metric("ai_core", "AI Core 利用率", "%", minimum=0, maximum=100))
        self.register(Metric("memory_used", "显存已用", "bytes", minimum=0))
        self.register(Metric("memory_total", "显存总量", "bytes", reducer="latest", minimum=0))

    def register(self, metric):
        if metric.key in self._items:
            raise ValueError("Duplicate metric key")
        if metric.reducer not in {"time_weighted", "latest", "max", "counter_delta"}:
            raise ValueError("Unsupported reducer")
        self._items[metric.key] = metric

    def describe(self):
        return [asdict(m) for m in self._items.values()]

    def validate(self, key, value):
        metric = self._items.get(key)
        if metric is None:
            raise DomainError("未注册的指标: " + key, 422)
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise DomainError("指标不是有效数值: " + key, 422)
        if metric.minimum is not None and value < metric.minimum or metric.maximum is not None and value > metric.maximum:
            raise DomainError("指标越界: " + key, 422)

    def aggregate(self, key, points, period=15):
        metric = self._items[key]
        valid = [p for p in points if p["quality"] == "ok" and p.get("value") is not None]
        if not valid:
            return {"value": None, "valid_seconds": 0}
        if metric.reducer == "latest":
            return {"value": valid[-1]["value"], "valid_seconds": 0}
        if metric.reducer == "max":
            return {"value": max(p["value"] for p in valid), "valid_seconds": 0}
        total = duration = delta = 0
        for a, b in zip(points, points[1:]):
            seconds = (b["sampled_at"] - a["sampled_at"]).total_seconds()
            if a["quality"] != "ok" or a.get("value") is None or seconds <= 0:
                continue
            covered = min(seconds, period)
            duration += covered
            total += a["value"] * covered
            if seconds <= period * 1.5 and b["quality"] == "ok" and b.get("value") is not None:
                delta += max(0, b["value"] - a["value"])
        return {"value": delta if metric.reducer == "counter_delta" else total / duration if duration else None,
                "valid_seconds": duration}
