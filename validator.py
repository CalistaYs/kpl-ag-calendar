#!/usr/bin/env python3
"""生成 ICS 之前的数据完整性校验。

设计原则：宁可因为这里判定"数据看起来不对"而停止更新，也不能把明显错误的
赛程覆盖到已有的 calendar.ics 上。任何一项检查失败，调用方都必须放弃本次更新。
"""

# KPL 目前最长打 BO5，单局比分分量不可能超过这个数；用来拦截"抓到了不相关
# 数字当成比分"这类错误（历史上出现过 60:60、85:80 这种明显不合理的比分）。
MAX_PLAUSIBLE_SCORE = 5

MIN_YEAR = 2020
MAX_YEAR = 2100


def validate_matches(matches, previous_count=None):
    """返回 (ok, errors, warnings)。errors 非空时调用方必须放弃本次更新。"""
    errors = []
    warnings = []

    if not matches:
        # 空数据本身不算错误——可能是新赛季赛程还没公布，由调用方决定如何处理。
        return True, errors, warnings

    seen = {}
    for m in matches:
        uid = m["scheduleid"]
        label = f"{m['home']} vs {m['away']}（{m['start']}）"
        if uid in seen:
            errors.append(f"重复 UID/scheduleid：{uid}（{seen[uid]} 与 {label} 冲突）")
        else:
            seen[uid] = label

        for side, score in (("主队", m["home_score"]), ("客队", m["away_score"])):
            if score is None:
                continue
            if not isinstance(score, int) or score < 0:
                errors.append(f"{uid} {side}比分格式非法：{score!r}")
            elif score > MAX_PLAUSIBLE_SCORE:
                errors.append(f"{uid} {side}比分超出合理范围（>{MAX_PLAUSIBLE_SCORE}）：{score}")

        if not (MIN_YEAR <= m["start"].year <= MAX_YEAR):
            errors.append(f"{uid} 开赛时间不合理：{m['start']}")

        if not m["home"] or not m["away"] or m["home"] == m["away"]:
            errors.append(f"{uid} 对阵双方队名不合法：{m['home']!r} vs {m['away']!r}")

    if previous_count is not None and previous_count > 0 and len(matches) < previous_count * 0.5:
        errors.append(
            f"本次解析到 {len(matches)} 场比赛，比上次 calendar.ics 里的 "
            f"{previous_count} 场少了一半以上，判定为异常，拒绝覆盖"
        )

    return (len(errors) == 0), errors, warnings
