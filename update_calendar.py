#!/usr/bin/env python3
"""KPL 成都AG超玩会赛程 -> calendar.ics 同步入口。

数据来源（fetch.py）、字段解析（match_parser.py）、数据校验（validator.py）、
ICS 渲染（ics_generator.py）拆在各自模块里，这里只负责把它们串起来，并严格执行
"任何一步看起来不对就放弃更新、保留现有 calendar.ics"的安全更新流程。
"""
import os
import re
import sys

from fetch import FetchError, discover_current_season, fetch_season_matches
from ics_generator import build_calendar
from match_parser import parse_matches
from validator import validate_matches

CALENDAR_PATH = "calendar.ics"
NEW_CALENDAR_PATH = "calendar.new.ics"

# 只有在自动探测（按当前日期猜 KPL{年}S1/S2，见 fetch.discover_current_season）
# 完全找不到数据时才会用到的兜底赛季 ID。建议每隔一段时间更新成"最近一个已知
# 能正常工作"的赛季 ID，避免探测逻辑将来失效时彻底没有兜底可用。
FALLBACK_SEASON_ID = "KPL2026S2"

# 如果未来出现挑战者杯/世界冠军杯等不遵循 KPL{年}S1/S2 命名规律的赛事，
# 自动探测大概率找不到数据；这时可以设置 KPL_SEASON_ID 环境变量（例如在
# GitHub Actions workflow 里加一行 env）手动指定赛季 ID，不需要改代码。
SEASON_ID_ENV_VAR = "KPL_SEASON_ID"


def count_existing_events():
    if not os.path.exists(CALENDAR_PATH):
        return 0
    with open(CALENDAR_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    return len(re.findall(r"^BEGIN:VEVENT", content, re.MULTILINE))


def resolve_season():
    override = os.environ.get(SEASON_ID_ENV_VAR)
    if override:
        print(f"[INFO] 使用手动指定的赛季 ID（{SEASON_ID_ENV_VAR} 环境变量）：{override}")
        try:
            return override, fetch_season_matches(override)
        except FetchError as exc:
            print(f"[ERROR] 手动指定的赛季 ID 拉取失败：{exc}")
            return None, None

    season_id, matches = discover_current_season(log=print)
    if season_id:
        print(f"[INFO] 自动探测到当前赛季：{season_id}")
        return season_id, matches

    print(f"[WARN] 自动探测失败，退回兜底赛季 ID：{FALLBACK_SEASON_ID}")
    try:
        return FALLBACK_SEASON_ID, fetch_season_matches(FALLBACK_SEASON_ID)
    except FetchError as exc:
        print(f"[ERROR] 兜底赛季 ID 也拉取失败：{exc}")
        return None, None


def main():
    season_id, raw_matches = resolve_season()
    if season_id is None:
        print("[ERROR] 无法获取任何赛季数据，保留现有 calendar.ics，退出。")
        sys.exit(1)

    print(f"[INFO] 发现比赛数量（{season_id} 全部战队）：{len(raw_matches)}")
    matches = parse_matches(raw_matches, warn=print)
    print(f"[INFO] 解析成功（AG 参赛场次）：{len(matches)}")

    previous_count = count_existing_events()
    ok, errors, warnings = validate_matches(matches, previous_count=previous_count)
    for w in warnings:
        print(f"[WARN] {w}")
    if not ok:
        for e in errors:
            print(f"[ERROR] {e}")
        print("[ERROR] 数据校验未通过，保留现有 calendar.ics，不覆盖，退出。")
        sys.exit(1)

    if not matches:
        print("[INFO] 当前赛季暂无 AG 的比赛数据（例如赛程尚未公布），保留现有 calendar.ics，不做改动。")
        return

    ics_text = build_calendar(matches)
    new_event_count = ics_text.count("BEGIN:VEVENT")
    if new_event_count != len(matches):
        print(
            f"[ERROR] 生成的 ICS 事件数（{new_event_count}）与解析到的比赛数"
            f"（{len(matches)}）不一致，保留现有 calendar.ics，不覆盖，退出。"
        )
        sys.exit(1)

    with open(NEW_CALENDAR_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(ics_text)
    os.replace(NEW_CALENDAR_PATH, CALENDAR_PATH)
    print(f"[INFO] 最终生成比赛：{new_event_count} 场，calendar.ics 已更新。")


if __name__ == "__main__":
    main()
