#!/usr/bin/env python3
"""生成 ICS 之前的数据完整性校验。

设计原则：宁可因为这里判定"数据看起来不对"而停止更新，也不能把明显错误的
赛程覆盖到已有的 calendar.ics 上。任何一项检查失败，调用方都必须放弃本次更新。
"""
from ics_generator import make_uid

# 赛制信息缺失时的默认比分上限。不写死"KPL 只有 BO5"，因为不同赛事/未来赛制
# 可能是 BO7、BO9 甚至更长；0-9 只是用来拦截"抓到了不相关数字当成比分"这类
# 明显错误（历史上出现过 60:60、85:80 这种比分），不代表任何具体赛制规则。
DEFAULT_MAX_SCORE = 9

MIN_YEAR = 2020
MAX_YEAR = 2100


def max_plausible_score(bo_total):
    """根据官方给出的赛制（bo_total，例如 BO5 对应 5）推算单方最多可能拿到的比分。

    BOn 赛制里先赢下 ceil(n/2) 局即获胜、比赛立即结束，所以单方最终比分不会超过
    ceil(n/2)（BO5 最高 3:x，BO7 最高 4:x，BO9 最高 5:x……随赛制变化，不写死数字）。
    bo_total 不是有效正整数（缺失/未来接口没给）时返回 None，调用方应退回
    DEFAULT_MAX_SCORE 这个更宽松的默认上限，而不是直接判定比分非法。
    """
    if not isinstance(bo_total, int) or bo_total <= 0:
        return None
    return (bo_total + 1) // 2


def validate_matches(matches, previous_count=None):
    """返回 (ok, errors, warnings)。errors 非空时调用方必须放弃本次更新。"""
    errors = []
    warnings = []

    if not matches:
        # 空数据本身不算错误——可能是新赛季赛程还没公布，由调用方决定如何处理。
        return True, errors, warnings

    seen = {}
    for m in matches:
        # 用最终会写进 ICS 的 UID（而不是裸 scheduleid）判重：不同赛事的 scheduleid
        # 理论上可能撞车，make_uid() 在这种情况下会自动拼上赛事代号前缀区分开，
        # 这里必须用同一套逻辑才能准确判断"是否真的会产生重复 UID"。
        uid = make_uid(m["season_id"], m["scheduleid"])
        label = f"{m['home']} vs {m['away']}（{m['start']}）"
        if uid in seen:
            errors.append(f"重复 UID：{uid}（{seen[uid]} 与 {label} 冲突）")
        else:
            seen[uid] = label

        bo_max = max_plausible_score(m.get("bo_total"))
        max_score = bo_max if bo_max is not None else DEFAULT_MAX_SCORE
        for side, score in (("主队", m["home_score"]), ("客队", m["away_score"])):
            if score is None:
                continue
            if not isinstance(score, int) or score < 0:
                errors.append(f"{uid} {side}比分格式非法：{score!r}")
            elif score > max_score:
                basis = f"按 BO{m['bo_total']} 推算" if bo_max is not None else "默认上限"
                errors.append(
                    f"{uid} {side}比分 {score} 超出合理范围"
                    f"（上限 {max_score}，{basis}），很可能是解析错误"
                )

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
