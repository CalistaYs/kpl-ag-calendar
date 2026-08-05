#!/usr/bin/env python3
"""每 30 分钟触发一次的智能调度入口（.github/workflows/update-calendar.yml 里
cron 是 '7,37 * * * *'）。

三种模式：

- FULL_REFRESH：在固定的 UTC 整点（0/6/12/18 点的 :07 那次运行）做一次完整扫描，
  用来发现新比赛/新赛事/改期/对手变化/官方数据修正——逻辑完全复用
  update_calendar.py 的 sync_calendar()。
- MATCH_REFRESH：不是完整扫描的时刻，但 calendar.ics 里有 AG 比赛正处于"临近
  开赛/进行中/刚结束但比分还没锁定"的窗口——只请求这些比赛涉及的 season_id
  （去重后），同样喂给 sync_calendar()，不写第二套解析/合并逻辑。
- SKIP：两者都不是，直接退出，不发任何请求、不碰 calendar.ics。

calendar.ics 不存在、解析不出任何事件、或者解析出来的事件里没有一个带
X-SEASON-ID（说明是这次改动之前生成的旧文件，还没有 X- 字段）时，安全回退成
FULL_REFRESH——这也是这个脚本第一次上线时"自动把旧 calendar.ics 迁移成带
X-SEASON-ID/X-MATCH-FINAL-SCORE 新格式"的方式，不需要额外写一次性迁移脚本。
"""
import datetime as dt
import os
import sys

from fetch import FetchError, fetch_season_matches, scan_all_seasons
from ics_generator import parse_calendar_events
from update_calendar import read_existing_calendar, sync_calendar

BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))

# 无状态：不记录"上次什么时候跑过完整扫描"，纯粹按 UTC 小时判断，同一个小时里
# 两次触发（:07 和 :37）只有 :07 那次算完整扫描时刻，避免半小时内跑两次。
FULL_REFRESH_HOURS_UTC = {0, 6, 12, 18}

# 高频比赛窗口边界，相对官方开赛时间：
PRE_MATCH_WINDOW = dt.timedelta(hours=1)     # 开赛前 1 小时起，进入 30 分钟档
DENSE_WINDOW_END = dt.timedelta(hours=5)     # 开赛后 5 小时内，30 分钟档（每次运行都刷新）
HOURLY_WINDOW_END = dt.timedelta(hours=12)   # 开赛后 5~12 小时，1 小时档（只在 :07 那次刷新）
# 超过开赛后 12 小时：不再高频跟踪，交给下一次完整扫描兜底。

FORCE_FULL_REFRESH_ENV = "FORCE_FULL_REFRESH"
NOW_OVERRIDE_ENV = "SMART_UPDATE_NOW"  # 测试/手动排查用：ISO 8601，覆盖"现在几点"


def parse_bool_env(value):
    """"true"/"1"/"yes"（大小写不敏感）算 True，其它（含空字符串、None、"false"）
    都算 False。不要用 Python 内置 bool("false")——那对任何非空字符串都是 True。
    """
    return (value or "").strip().lower() in ("1", "true", "yes")


def resolve_now():
    """得到本次运行要用的"现在"，带 Asia/Shanghai 时区。

    支持 SMART_UPDATE_NOW 环境变量覆盖（ISO 8601，比如
    "2026-08-06T20:00:00+08:00"），方便模拟不同时间点跑各种场景，不用真的等。
    """
    override = os.environ.get(NOW_OVERRIDE_ENV, "").strip()
    if override:
        parsed = dt.datetime.fromisoformat(override)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TZ)
        return parsed.astimezone(BEIJING_TZ)
    return dt.datetime.now(BEIJING_TZ)


def is_on_hour_run(now):
    """:07 那次运行返回 True，:37 那次返回 False。UTC/北京时间的"分钟数"完全
    一样（时区偏移是整小时），不管用哪个时区取 now 都一样，不需要额外换算。
    """
    return now.minute < 30


def is_full_refresh_time(now, force=False):
    if force:
        return True
    return now.astimezone(dt.timezone.utc).hour in FULL_REFRESH_HOURS_UTC and is_on_hour_run(now)


def classify_window(now, start, on_hour_run):
    """返回 "dense"（30 分钟档）/ "hourly"（1 小时档，只在 :07 那次生效）/
    None（不在任何跟踪窗口内）。now/start 都必须是带时区的 datetime。
    """
    delta = now - start
    if -PRE_MATCH_WINDOW <= delta <= DENSE_WINDOW_END:
        return "dense"
    if DENSE_WINDOW_END < delta <= HOURLY_WINDOW_END:
        return "hourly" if on_hour_run else None
    return None


def find_tracked_matches(now, ics_text):
    """从 calendar.ics 的内容里找出仍处于高频跟踪窗口、且还没有确认最终比分的
    AG 比赛。ics_text 为空、解析不出任何事件、或者一个带 X-SEASON-ID 的事件都
    没有（说明是旧格式文件，这个函数没法判断该不该跟踪）时返回 None——调用方
    应该据此回退到 FULL_REFRESH，而不是当成"没有比赛"直接 SKIP。
    """
    if not ics_text:
        return None
    try:
        events = parse_calendar_events(ics_text)
    except Exception as exc:
        print(f"[WARN] 解析 calendar.ics 失败（{exc}），回退到完整扫描")
        return None

    total_vevents = ics_text.count("BEGIN:VEVENT")
    events_with_season = [ev for ev in events if ev["season_id"]]
    if total_vevents == 0 or not events_with_season:
        return None

    on_hour_run = is_on_hour_run(now)
    tracked = []
    for ev in events_with_season:
        if ev["final"]:
            continue
        window = classify_window(now, ev["start"], on_hour_run)
        if window:
            tracked.append({**ev, "window": window})
    return tracked


def fetch_seasons(season_ids, log=print):
    """只请求指定的 season_id 列表（已经去重），单个赛事请求失败只记 WARN、跳过，
    不会把它当成"这个赛事现在没有比赛"传给 sync_calendar()——那样会被
    merge_calendars() 当成"这个赛事真的清空了"，误删现有事件。全部失败时返回
    空字典，调用方要能正确处理（不写入、不清空 calendar.ics）。
    """
    results = {}
    for season_id in season_ids:
        try:
            matches = fetch_season_matches(season_id)
        except FetchError as exc:
            log(f"[WARN] 赛事 {season_id} 拉取失败，跳过（不影响该赛事现有事件）：{exc}")
            continue
        if not matches:
            log(f"[WARN] 赛事 {season_id} 这次返回了空数据，跳过（不当成该赛事已清空）")
            continue
        log(f"[INFO] 赛事 {season_id} 拉取成功，共 {len(matches)} 场比赛")
        results[season_id] = matches
    return results


def _run_full_refresh():
    print("[INFO] 本次模式：FULL_REFRESH")
    season_results = scan_all_seasons(log=print)
    if not season_results:
        print(
            "[ERROR] 扫描的所有候选赛事 ID 都没有拿到数据（可能签名失效、网络故障，"
            "或官方接口发生了变化），保留现有 calendar.ics，退出。"
        )
        return False
    return sync_calendar(season_results, preserve_unchanged_dtstamp=False)


def _run_match_refresh(tracked):
    print("[INFO] 本次模式：MATCH_REFRESH")
    season_ids = sorted({ev["season_id"] for ev in tracked})
    print(f"[INFO] 处于跟踪窗口的比赛共 {len(tracked)} 场，涉及赛事 {len(season_ids)} 个：{', '.join(season_ids)}")
    for ev in tracked:
        old_score = f"{ev['score'][0]}:{ev['score'][1]}" if ev["score"] else "无"
        print(
            f"[INFO]   UID={ev['uid']}  开赛={ev['start'].strftime('%Y-%m-%d %H:%M %z')}  "
            f"窗口={ev['window']}  查询前比分={old_score}（final={ev['final']}）"
        )

    season_results = fetch_seasons(season_ids, log=print)
    if not season_results:
        print("[ERROR] 涉及的赛事全部拉取失败，保留现有 calendar.ics，退出。")
        return False

    ok = sync_calendar(season_results, preserve_unchanged_dtstamp=True)
    if ok:
        # 查询后的状态：重新读一次 calendar.ics，对照之前打印的"查询前"状态。
        after_text = read_existing_calendar() or ""
        after_by_uid = {ev["uid"]: ev for ev in parse_calendar_events(after_text)}
        for ev in tracked:
            after = after_by_uid.get(ev["uid"])
            if not after:
                continue
            new_score = f"{after['score'][0]}:{after['score'][1]}" if after["score"] else "无"
            print(f"[INFO]   查询后 UID={ev['uid']}  比分={new_score}（final={after['final']}）")
    return ok


def main():
    now = resolve_now()
    print(f"[INFO] 当前北京时间：{now.strftime('%Y-%m-%d %H:%M:%S %z')}")

    force_full_refresh = parse_bool_env(os.environ.get(FORCE_FULL_REFRESH_ENV))
    if is_full_refresh_time(now, force=force_full_refresh):
        ok = _run_full_refresh()
        sys.exit(0 if ok else 1)

    ics_text = read_existing_calendar()
    tracked = find_tracked_matches(now, ics_text)
    if tracked is None:
        print("[WARN] calendar.ics 不存在或无法可靠解析（可能是旧格式，还没有 X-SEASON-ID），回退执行 FULL_REFRESH")
        ok = _run_full_refresh()
        sys.exit(0 if ok else 1)

    if not tracked:
        print("[INFO] 本次模式：SKIP")
        print("[INFO] 当前没有需要高频跟踪的 AG 比赛，本次跳过")
        sys.exit(0)

    ok = _run_match_refresh(tracked)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
