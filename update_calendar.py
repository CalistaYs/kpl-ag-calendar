#!/usr/bin/env python3
"""KPL 成都AG超玩会赛程 -> calendar.ics 同步入口。

数据来源（fetch.py）、字段解析（match_parser.py）、数据校验（validator.py）、
ICS 渲染与合并（ics_generator.py）拆在各自模块里，这里只负责把它们串起来，并严格
执行"任何一步看起来不对就放弃更新、保留现有 calendar.ics"的安全更新流程。

每次运行只会向官方接口请求"当前赛季"的比赛（见 fetch.discover_current_season），
但写回 calendar.ics 时会跟文件里已有的内容合并，而不是整份覆盖：属于其它赛季、
这次根本没请求过的历史比赛原样保留，不会因为赛季切换就从日历里消失；属于这次
请求的赛季的比赛，则以这次抓到的完整结果为准（新增/更新/如果某场比赛从官方
接口消失了也会跟着从日历里移除），细节见 ics_generator.merge_calendars。
"""
import os
import sys

from fetch import FetchError, discover_current_season, fetch_season_matches
from ics_generator import build_calendar, merge_calendars
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


def read_existing_calendar():
    if not os.path.exists(CALENDAR_PATH):
        return None
    with open(CALENDAR_PATH, "r", encoding="utf-8") as f:
        return f.read()


def count_events_for_season(ics_text, season_id):
    """统计现有 calendar.ics 里属于某个赛季的比赛数量（UID 里带该赛季 scheduleid
    前缀），用作"这次抓到的比赛是不是突然少了一大截"的校验基准。

    必须按赛季统计，不能跟日历里累计的全部历史比赛数比——不然赛季一多、历史
    事件越攒越多，新赛季刚开赛、比赛数量还很少时就会被永远误判成异常。
    """
    if not ics_text:
        return 0
    needle = f"UID:kpl-{season_id.lower()}"
    return sum(1 for line in ics_text.splitlines() if line.startswith(needle))


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

    existing_text = read_existing_calendar()
    previous_count = count_events_for_season(existing_text, season_id)
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

    new_ics_text = build_calendar(matches)
    new_event_count = new_ics_text.count("BEGIN:VEVENT")
    if new_event_count != len(matches):
        print(
            f"[ERROR] 生成的 ICS 事件数（{new_event_count}）与解析到的比赛数"
            f"（{len(matches)}）不一致，保留现有 calendar.ics，不覆盖，退出。"
        )
        sys.exit(1)

    existing_total = existing_text.count("BEGIN:VEVENT") if existing_text else 0
    other_seasons_count = existing_total - previous_count  # 其它赛季、这次完全没触及的历史比赛数

    if existing_text:
        merged_text = merge_calendars(existing_text, new_ics_text, season_id)
    else:
        merged_text = new_ics_text
    merged_event_count = merged_text.count("BEGIN:VEVENT")

    expected_count = other_seasons_count + new_event_count
    if merged_event_count != expected_count:
        print(
            f"[ERROR] 合并后事件数（{merged_event_count}）与预期不一致（其它赛季历史比赛 "
            f"{other_seasons_count} 场 + 本次 {season_id} 比赛 {new_event_count} 场 = "
            f"{expected_count} 场），合并逻辑异常，保留现有 calendar.ics，不覆盖，退出。"
        )
        sys.exit(1)

    with open(NEW_CALENDAR_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(merged_text)
    os.replace(NEW_CALENDAR_PATH, CALENDAR_PATH)
    print(
        f"[INFO] 本次抓取到的 {season_id} 比赛：{new_event_count} 场；"
        f"合并历史赛季后 calendar.ics 总比赛数：{merged_event_count} 场。"
    )


if __name__ == "__main__":
    main()
