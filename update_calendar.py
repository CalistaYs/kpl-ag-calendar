#!/usr/bin/env python3
"""成都AG超玩会全官方赛事日历 -> calendar.ics 同步入口。

数据来源（fetch.py）、字段解析与目标战队识别（match_parser.py）、数据校验
（validator.py）、ICS 渲染与合并（ics_generator.py）拆在各自模块里，这里只负责
把它们串起来，并严格执行"任何一步看起来不对就放弃更新、保留现有 calendar.ics"
的安全更新流程。

每次运行会扫描一批候选赛事 ID（多个年份 x 多种赛事代号模板，覆盖 KPL 春/夏季赛、
年度总决赛、电竞世界杯、国际邀请赛等，见 fetch.scan_all_seasons），凡是官方接口
有数据的都会被收录，再从中筛出目标战队（默认成都AG超玩会，含国际赛事别名，见
match_parser.is_target_team）参赛的场次，合并进 calendar.ics——不再局限于单一的
"当前 KPL 赛季"，日历本身也不会因为赛季/赛事切换就丢失历史比赛。
"""
import os
import sys

from fetch import scan_all_seasons
from ics_generator import build_calendar, extract_uids, merge_calendars
from match_parser import TARGET_TEAM, parse_matches
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


def main():
    season_results = scan_all_seasons(log=print)
    if not season_results:
        print(
            "[ERROR] 扫描的所有候选赛事 ID 都没有拿到数据（可能签名失效、网络故障，"
            "或官方接口发生了变化），保留现有 calendar.ics，退出。"
        )
        sys.exit(1)

    valid_season_ids = sorted(season_results.keys())
    print(f"[INFO] 本次有效赛事共 {len(valid_season_ids)} 个：{', '.join(valid_season_ids)}")

    all_raw_matches = []
    for season_id, raw_matches in season_results.items():
        for raw in raw_matches:
            raw["_season_id"] = season_id
        all_raw_matches.extend(raw_matches)

    matches, skipped = parse_matches(all_raw_matches, warn=print)

    ag_counts_by_season = {}
    for m in matches:
        ag_counts_by_season[m["season_id"]] = ag_counts_by_season.get(m["season_id"], 0) + 1
    for season_id in valid_season_ids:
        print(
            f"[INFO] {season_id}：全部战队比赛 {len(season_results[season_id])} 场，"
            f"目标战队（{TARGET_TEAM}）参赛 {ag_counts_by_season.get(season_id, 0)} 场"
        )
    print(f"[INFO] 全部赛事合计目标战队参赛场次：{len(matches)}；解析异常跳过：{skipped} 场")

    existing_text = read_existing_calendar()
    existing_uids_in_scope = uids_for_seasons(existing_text, valid_season_ids)
    previous_count = len(existing_uids_in_scope)
    ok, errors, warnings = validate_matches(matches, previous_count=previous_count)
    for w in warnings:
        print(f"[WARN] {w}")
    if not ok:
        for e in errors:
            print(f"[ERROR] {e}")
        print("[ERROR] 数据校验未通过，保留现有 calendar.ics，不覆盖，退出。")
        sys.exit(1)

    if not matches:
        print("[INFO] 本次没有扫描到任何目标战队的比赛，保留现有 calendar.ics，不做改动。")
        return

    new_ics_text = build_calendar(matches)
    new_event_count = new_ics_text.count("BEGIN:VEVENT")
    if new_event_count != len(matches):
        print(
            f"[ERROR] 生成的 ICS 事件数（{new_event_count}）与解析到的比赛数"
            f"（{len(matches)}）不一致，保留现有 calendar.ics，不覆盖，退出。"
        )
        sys.exit(1)

    existing_total = existing_text.count("BEGIN:VEVENT") if existing_text else 0
    other_events_count = existing_total - previous_count  # 其它赛事、这次完全没触及的历史比赛数

    new_uids = extract_uids(new_ics_text)
    updated_count = len(new_uids & existing_uids_in_scope)
    added_count = len(new_uids - existing_uids_in_scope)
    removed_count = len(existing_uids_in_scope - new_uids)
    print(
        f"[INFO] 本次扫描到的赛事范围内：更新 {updated_count} 场，新增 {added_count} 场，"
        f"移除 {removed_count} 场（比如已取消/不再由官方接口返回）"
    )

    if existing_text:
        merged_text = merge_calendars(existing_text, new_ics_text, valid_season_ids)
    else:
        merged_text = new_ics_text
    merged_event_count = merged_text.count("BEGIN:VEVENT")

    expected_count = other_events_count + new_event_count
    if merged_event_count != expected_count:
        print(
            f"[ERROR] 合并后事件数（{merged_event_count}）与预期不一致（其它赛事历史比赛 "
            f"{other_events_count} 场 + 本次扫描到的比赛 {new_event_count} 场 = "
            f"{expected_count} 场），合并逻辑异常，保留现有 calendar.ics，不覆盖，退出。"
        )
        sys.exit(1)

    with open(NEW_CALENDAR_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(merged_text)
    os.replace(NEW_CALENDAR_PATH, CALENDAR_PATH)
    print(f"[INFO] 最终合并后 calendar.ics 共有 {merged_event_count} 场比赛。")


if __name__ == "__main__":
    main()
