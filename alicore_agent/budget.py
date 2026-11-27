"""每日 DeepSeek 额度锁 + 记账。纯本地 json，零成本，不调任何 API。

为什么单独一个文件：早先版本的记账逻辑混在主程序里，改起来要动主文件。
这里单起一个模块，llm.py 只负责调它，账目和刹车逻辑都在这里。

和早先版本的一个关键区别——旧算式把 prompt_tokens 全额按输入价算，
又把 cache_hit 再加一遍，命中高的时候高估约 3.9 倍。这里分成三段算：

    未命中输入 × 1 元/M + 命中输入 × 0.1 元/M + 输出 × 2 元/M

这样账才是真的，不会因为怕超支而把额度定得太保守。

【2026-11-20 改】原来只存「今天」这一条，跨天直接清零——于是「这个月花了多少」
这个数根本不存在，用户只能靠感觉。现在改成按天的账本：

    {"days": {"2026-11-18": {total, calls, prompt, completion, cache_hit}, ...}}

老格式 {date, total, calls} 会自动迁移成新结构。当日额度的语义没变，
over_limit() 看的仍然是今天。
"""

import json
import os
import time

DEFAULT_DAILY_LIMIT = 10.0   # 元/天。这是练手项目，给个刹车就够，别把钱烧在调试上
_SPEND_FILE = "deepseek_spending.json"
_KEEP_DAYS = 400             # 留一年多，够看年度趋势，也不会把文件撑成垃圾场

# DeepSeek 价格（元/百万 token）。改价时只改这里。
PRICE_MISS = 1.0
PRICE_HIT = 0.1
PRICE_OUT = 2.0


def _today():
    return time.strftime("%Y-%m-%d")


def _empty_day():
    return {"total": 0.0, "calls": 0, "prompt": 0, "completion": 0, "cache_hit": 0}


class DailyBudget:
    """今天烧了多少钱；超了就拦，别无声地继续烧。"""

    def __init__(self, base_dir, limit=None):
        self.path = os.path.join(base_dir, _SPEND_FILE)
        try:
            self.limit = float(limit) if limit else DEFAULT_DAILY_LIMIT
        except (TypeError, ValueError):
            self.limit = DEFAULT_DAILY_LIMIT
        self._cache = None

    # ---------------- 账本读写 ----------------
    def _load_all(self):
        """读整个账本 → {日期: 当日记录}。老格式自动迁移。"""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        if isinstance(raw.get("days"), dict):
            return raw["days"]
        # 兼容旧格式：{"date": "...", "total": ..., "calls": ...}
        d = raw.get("date")
        if d and "total" in raw:
            return {d: {
                "total": float(raw.get("total", 0.0)),
                "calls": int(raw.get("calls", 0)),
                "prompt": 0,
                "completion": 0,
                "cache_hit": 0,
            }}
        return {}

    def _load(self):
        if self._cache is not None:
            return self._cache
        self._cache = dict(_empty_day())
        self._cache.update((self._load_all().get(_today()) or {}))
        return self._cache

    def _flush(self):
        if self._cache is None:
            return
        try:
            days = self._load_all()
            days[_today()] = self._cache
            if len(days) > _KEEP_DAYS:
                for k in sorted(days)[:-_KEEP_DAYS]:
                    days.pop(k, None)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"days": days}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------------- 记账 ----------------
    def record(self, prompt_tokens, completion_tokens, cache_hit=0):
        """按 DeepSeek 真实价记账。返回这一笔的金额（元）。"""
        p = int(prompt_tokens or 0)
        c = int(completion_tokens or 0)
        h = int(cache_hit or 0)
        miss = max(0, p - h)          # 只有没命中缓存的部分才付全价
        cost = (miss * PRICE_MISS + h * PRICE_HIT + c * PRICE_OUT) / 1e6
        d = self._load()
        d["total"] = round(float(d.get("total", 0.0)) + cost, 6)
        d["calls"] = int(d.get("calls", 0)) + 1
        d["prompt"] = int(d.get("prompt", 0)) + p
        d["completion"] = int(d.get("completion", 0)) + c
        d["cache_hit"] = int(d.get("cache_hit", 0)) + h
        self._flush()
        return cost

    # ---------------- 查询 ----------------
    def total(self):
        return float(self._load().get("total", 0.0))

    def calls(self):
        return int(self._load().get("calls", 0))

    def over_limit(self):
        return self.total() >= self.limit

    def describe(self):
        return f"今日 {self.total():.4f} 元 / 上限 {self.limit:.2f} 元（{self.calls()} 次）"

    def report(self, days=30):
        """最近 N 天的汇总，返回可直接打印的多行文本。

        「月耗 10 元」这件事，只有账本能回答；靠感觉判断一定会失真。
        这里不调模型，纯算术。
        """
        all_days = self._load_all()
        keys = sorted(all_days)[-days:]
        if not keys:
            return "账本还是空的（没记到任何一次 DeepSeek 调用）。"

        sum_total = sum(float(all_days[k].get("total", 0)) for k in keys)
        sum_calls = sum(int(all_days[k].get("calls", 0)) for k in keys)
        sum_prompt = sum(int(all_days[k].get("prompt", 0)) for k in keys)
        sum_hit = sum(int(all_days[k].get("cache_hit", 0)) for k in keys)
        hit_rate = (sum_hit / sum_prompt * 100) if sum_prompt else 0

        lines = [
            f"最近 {len(keys)} 天（{keys[0]} ~ {keys[-1]}）",
            f"  花费    {sum_total:.4f} 元",
            f"  调用    {sum_calls} 次，平均 {sum_total / max(1, sum_calls):.5f} 元/次",
        ]
        if sum_prompt:
            lines.append(f"  缓存    命中 {sum_hit:,} / {sum_prompt:,} = {hit_rate:.1f}%")
        # 日均和按 10 元/天折算的月耗，才是用户真正在意的数
        per_day = sum_total / max(1, len(keys))
        lines += [
            f"  日均    {per_day:.4f} 元 → 按这个节奏月耗约 {per_day * 30:.2f} 元",
            "",
            "  逐日：",
        ]
        for k in keys[-10:]:
            v = all_days[k]
            lines.append(f"    {k}  {float(v.get('total', 0)):.4f} 元  {int(v.get('calls', 0))} 次")
        if len(keys) > 10:
            lines.append(f"    （只列最近 10 天，共 {len(keys)} 天）")
        return "\n".join(lines)
