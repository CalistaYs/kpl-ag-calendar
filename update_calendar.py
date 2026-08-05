#!/usr/bin/env python3
"""成都AG超玩会全官方赛事日历 -> calendar.ics 同步入口（完整扫描版）。

数据来源（fetch.py）、字段解析与目标战队识别（match_parser.py）、数据校验
（validator.py）、ICS 渲染与合并（ics_generator.py）拆在各自模块里。核心的
"给定一批赛事的原始数据，解析/校验/渲染/合并/写入"逻辑在 sync_calendar()，
main() 只是在此之上加了"先做完整扫描拿到全部候选赛事的数据"这一步——这样
smart_update.py 的高频模式可以直接复用 sync_calendar()，不需要另一套逻辑。

任何一步看起来不对，都会保留现有 calendar.ics、不覆盖。
"""
import os
import sys

from fetch import scan_all_seasons
from ics_generator import build_calendar, extract_uids, merge_calendars, parse_calendar_events
from match_parser import TARGET_TEAM, list_teams, parse_matches
from validator import validate_matches

CALENDAR_PATH = "calendar.ics"
NEW_CALENDAR_PATH = "calendar.new.ics"


def read_existing_calendar():
    if not os.path.exists(CALENDAR_PATH):
        return None
    with open(CALENDAR_PATH, "r", encoding="utf-8") as f:
        return f.read()


def uids_for_seasons(ics_text, season_ids):
    """现有 calendar.ics 里，UID 属于某一批赛事（scheduleid 前缀匹配）的事件集合。

    用于两处：(1) "这次抓到的比赛是不是突然少了一大截" 的校验基准——必须只跟这次
    实际扫描到的赛事比，不能跟日历里累计的全部历史比赛数比，不然赛事一多、历史
    事件越攒越多，新赛季/新赛事刚开始、比赛数量还很少时就会被永远误判成异常；
    (2) 统计这次更新了/新增了/移除了多少场比赛。
    """
    if not ics_text or not season_ids:
        return set()
    prefixes = tuple(f"kpl-{sid.lower()}" for sid in season_ids)
    return {uid for uid in extract_uids(ics_text) if uid.startswith(prefixes)}


def _existing_final_states(ics_text):
    """把 calendar.ics 解析成 {uid: {"score":..., "final":...}}，喂给
    build_calendar() 做"比分两次确认"判断。calendar.ics 不存在，或者某场比赛
    以前没有 X-MATCH-SCORE/X-MATCH-FINAL-SCORE（旧格式、或者这是新比赛），
    在结果里就是缺这个 key——调用方（_resolve_final_score_state）会按
    "没有历史记录"处理，不会报错。
    """
    if not ics_text:
        return {}
    return {ev["uid"]: {"score": ev["score"], "final": ev["final"]} for ev in parse_calendar_events(ics_text)}


def sync_calendar(season_results, preserve_unchanged_dtstamp=False):
    """给定 {season_id: 该赛事原始比赛列表}，跑完整的解析 -> 校验 -> 渲染 -> 合并
    -> 写入流程。season_results 可以是"完整扫描"的全部结果，也可以是高频模式
    只请求了少数几个赛事的结果——逻辑完全一样，不区分调用方是谁。

    返回 True 表示"处理完成，没有发现问题"（包括"这批赛事里没有目标战队比赛，
    没做任何改动"这种正常的空结果）；返回 False 表示校验没通过或者出现内部不
    一致，调用方应该把这当成失败处理（非零退出），calendar.ics 在任何一种失败
    路径下都不会被覆盖。
    """
    if not season_results:
        print("[INFO] 没有传入任何赛事数据，保留现有 calendar.ics，不做改动。")
        return True

    valid_season_ids = sorted(season_results.keys())
    print(f"[INFO] 本次处理赛事共 {len(valid_season_ids)} 个：{', '.join(valid_season_ids)}")

    all_raw_matches = []
    for season_id, raw_matches in season_results.items():
        for raw in raw_matches:
            raw["_season_id"] = season_id
        all_raw_matches.extend(raw_matches)

    matches, skipped = parse_matches(all_raw_matches, warn=print)

    for m in matches:
        print(f"[INFO] {m['home']} vs {m['away']}  阶段={m['stage_label'] or '未识别'}")

    ag_counts_by_season = {}
    for m in matches:
        ag_counts_by_season[m["season_id"]] = ag_counts_by_season.get(m["season_id"], 0) + 1
    for season_id in valid_season_ids:
        print(
            f"[INFO] {season_id}：全部战队比赛 {len(season_results[season_id])} 场，"
            f"目标战队（{TARGET_TEAM}）参赛 {ag_counts_by_season.get(season_id, 0)} 场"
        )
        team_flags = list_teams(season_results[season_id])
        team_list = ", ".join(f"[{name}]" if is_match else name for name, is_match in team_flags)
        print(f"[INFO] {season_id} 参赛队伍（{len(team_flags)} 支，[方括号]=已匹配目标战队）：{team_list}")
    print(f"[INFO] 本次合计目标战队参赛场次：{len(matches)}；解析异常跳过：{skipped} 场")

    existing_text = read_existing_calendar()
    existing_uids_in_scope = uids_for_seasons(existing_text, valid_season_ids)
    previous_count = len(existing_uids_in_scope)
    ok, errors, warnings = validate_matches(matches, previous_count=previous_count)
    for w in warnings:
        print(f"[WARN] {w}")
    if not ok:
        for e in errors:
            print(f"[ERROR] {e}")
        print("[ERROR] 数据校验未通过，保留现有 calendar.ics，不覆盖。")
        return False

    if not matches:
        print("[INFO] 本次没有扫描到任何目标战队的比赛，保留现有 calendar.ics，不做改动。")
        return True

    existing_final_states = _existing_final_states(existing_text)
    new_ics_text = build_calendar(matches, existing_final_states=existing_final_states)
    new_event_count = new_ics_text.count("BEGIN:VEVENT")
    if new_event_count != len(matches):
        print(
            f"[ERROR] 生成的 ICS 事件数（{new_event_count}）与解析到的比赛数"
            f"（{len(matches)}）不一致，保留现有 calendar.ics，不覆盖。"
        )
        return False

    existing_total = existing_text.count("BEGIN:VEVENT") if existing_text else 0
    other_events_count = existing_total - previous_count  # 其它赛事、这次完全没触及的历史比赛数

    new_uids = extract_uids(new_ics_text)
    updated_count = len(new_uids & existing_uids_in_scope)
    added_count = len(new_uids - existing_uids_in_scope)
    removed_count = len(existing_uids_in_scope - new_uids)
    print(
        f"[INFO] 本次处理的赛事范围内：更新 {updated_count} 场，新增 {added_count} 场，"
        f"移除 {removed_count} 场（比如已取消/不再由官方接口返回）"
    )

    if existing_text:
        merged_text = merge_calendars(
            existing_text, new_ics_text, valid_season_ids,
            preserve_unchanged_dtstamp=preserve_unchanged_dtstamp,
        )
    else:
        merged_text = new_ics_text
    merged_event_count = merged_text.count("BEGIN:VEVENT")

    expected_count = other_events_count + new_event_count
    if merged_event_count != expected_count:
        print(
            f"[ERROR] 合并后事件数（{merged_event_count}）与预期不一致（其它赛事历史比赛 "
            f"{other_events_count} 场 + 本次处理的比赛 {new_event_count} 场 = "
            f"{expected_count} 场），合并逻辑异常，保留现有 calendar.ics，不覆盖。"
        )
        return False

    changed = merged_text != existing_text
    print(f"[INFO] calendar.ics 是否发生变化：{'是' if changed else '否'}")
    if not changed:
        return True

    with open(NEW_CALENDAR_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(merged_text)
    os.replace(NEW_CALENDAR_PATH, CALENDAR_PATH)
    print(f"[INFO] 最终合并后 calendar.ics 共有 {merged_event_count} 场比赛。")
    return True


def main():
    season_results = scan_all_seasons(log=print)
    if not season_results:
        print(
            "[ERROR] 扫描的所有候选赛事 ID 都没有拿到数据（可能签名失效、网络故障，"
            "或官方接口发生了变化），保留现有 calendar.ics，退出。"
        )
        sys.exit(1)

    # 挑战者杯目前没有稳定命中的固定代号，不能只检查某个写死的 ID 是否在候选列表
    # 里，而是看这次实际扫描到的赛事里，有没有某个赛事的官方 season 标签带"挑战者
    # 杯"三个字。只在完整扫描（这里）里判断——高频模式一次只处理一两个已经确定的
    # 赛事，不是一次新的"发现"扫描，打这条日志没有意义。
    challenge_cup_season_id = next(
        (
            season_id
            for season_id, raw_matches in season_results.items()
            if raw_matches and "挑战者杯" in (raw_matches[0].get("season") or "")
        ),
        None,
    )
    if challenge_cup_season_id:
        print(f"[INFO] Found Challenge Cup seasonid: {challenge_cup_season_id}")
    else:
        print("[WARN] 未发现挑战者杯 seasonid")

    ok = sync_calendar(season_results)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
