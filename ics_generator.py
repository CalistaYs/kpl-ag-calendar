#!/usr/bin/env python3
"""把校验通过的比赛列表渲染成 RFC5545 格式的 .ics 文本，并支持跨赛事/跨赛季合并。"""
import datetime as dt
import re

from match_parser import AG_SHORT_NAME, is_target_team, opponent_of, short_team_name

DEFAULT_DURATION_HOURS = 3
ALARM_OFFSETS = ("-PT1H", "-PT30M")
KPL_URL = "https://pvp.qq.com/match/kpl/"


def ics_escape(text):
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def make_uid(season_id, scheduleid):
    """构造这场比赛的 UID。

    scheduleid（如 KPL2026S2M3W3D3）是腾讯官方赛程系统给这场比赛分配的稳定 ID，
    通常本身就以 season_id 开头，这种情况下直接用它就已经跨赛事唯一，不会随开赛
    时间/地点/比分变化。只有 scheduleid 不是这样（理论上不该发生，但防御性地
    处理一下）时才显式拼上 season_id 前缀，避免不同赛事之间万一撞出相同的
    scheduleid——这样也不会改动已经发布过的、scheduleid 本就带季号前缀的 UID。
    """
    slug = scheduleid.strip().lower()
    season_prefix = season_id.strip().lower()
    if season_prefix and not slug.startswith(season_prefix):
        slug = f"{season_prefix}-{slug}"
    return f"kpl-{slug}@calistays.github"


def build_calendar(matches, dtstamp=None):
    stamp = (dtstamp or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CalistaYs//KPL AG Calendar//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:成都AG超玩会官方赛事日历",
        "X-WR-TIMEZONE:Asia/Shanghai",
        "X-PUBLISHED-TTL:PT6H",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Shanghai",
        "X-LIC-LOCATION:Asia/Shanghai",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "DTSTART:19700101T000000",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for m in matches:
        start = m["start"]
        end = start + dt.timedelta(hours=DEFAULT_DURATION_HOURS)
        opponent = opponent_of(m)
        opponent_short = short_team_name(opponent)
        summary = f"{AG_SHORT_NAME} VS {opponent_short}"
        location = m["location"]

        season_line = f"KPL {m['season_label']}".strip() if m["season_label"] else "KPL"
        desc_lines = [
            f"{season_line}。",
            f"{m['home']} vs {m['away']}。",
        ]
        if m["stage_label"]:
            desc_lines.append(f"阶段：{m['stage_label']}。")
        desc_lines.append(f"开赛时间：{start.strftime('%H:%M')}（北京时间 GMT+8，官方数据）。")
        if location:
            desc_lines.append(f"比赛地点：{location}")
        if m["home_score"] is not None and m["away_score"] is not None:
            is_ag_home = is_target_team(m["home"])
            ag_score = m["home_score"] if is_ag_home else m["away_score"]
            opp_score = m["away_score"] if is_ag_home else m["home_score"]
            desc_lines.append(f"比赛结果：{AG_SHORT_NAME} {ag_score}:{opp_score} {opponent_short}")
        desc_lines.append(f"官方观赛入口：{KPL_URL}")
        detail = "\n".join(desc_lines)

        alarm_description = f"{summary} 即将开始"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{make_uid(m['season_id'], m['scheduleid'])}",
            f"DTSTAMP:{stamp}",
            f"SUMMARY:{ics_escape(summary)}",
            f"DTSTART;TZID=Asia/Shanghai:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Shanghai:{end.strftime('%Y%m%dT%H%M%S')}",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            f"URL:{KPL_URL}",
        ])
        if location:
            lines.append(f"LOCATION:{ics_escape(location)}")
        lines.append(f"DESCRIPTION:{ics_escape(detail)}")
        for offset in ALARM_OFFSETS:
            lines.extend([
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{ics_escape(alarm_description)}",
                f"TRIGGER:{offset}",
                "END:VALARM",
            ])
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


_VEVENT_RE = re.compile(r"BEGIN:VEVENT\r?\n.*?END:VEVENT\r?\n", re.S)
_UID_RE = re.compile(r"^UID:(.+?)\r?$", re.M)
_DTSTART_RE = re.compile(r"^DTSTART[^:]*:(\d{8}T\d{6})", re.M)


def _extract_vevents(ics_text):
    """把一段 ICS 文本按 UID 拆成 {uid: 原始 VEVENT 文本块} 的字典。"""
    blocks = {}
    for block in _VEVENT_RE.findall(ics_text):
        uid_match = _UID_RE.search(block)
        if uid_match:
            blocks[uid_match.group(1).strip()] = block
    return blocks


def _vevent_sort_key(block):
    match = _DTSTART_RE.search(block)
    return match.group(1) if match else ""


def extract_uids(ics_text):
    """返回一段 ICS 文本里所有 VEVENT 的 UID 集合，供调用方统计更新/新增/移除数量。"""
    return set(_extract_vevents(ics_text).keys())


def merge_calendars(existing_ics_text, new_ics_text, refreshed_season_ids):
    """把这次新抓到的比赛（new_ics_text，refreshed_season_ids 这些赛事的完整赛程）
    合并进已有日历（existing_ics_text）。

    - 不属于 refreshed_season_ids 的历史比赛（UID 里的赛事代号前缀是其它赛事/
      这次没扫描到或扫描失败的赛事）——原样保留，不会因为赛事/赛季切换、或者
      某个赛事这次临时拉取失败，就从日历里消失；未被触及的历史事件也保留原有
      的 DTSTAMP，不会被当成"刚生成"。
    - 属于 refreshed_season_ids 的比赛，用这次抓到的结果完整替换旧版本：因为每次
      都是拉取"该赛事的全部比赛"（不是增量），所以这次没有出现的旧记录（比如
      被取消的比赛）应该跟着消失，不能残留成永远删不掉的僵尸事件；同 UID 有
      更新的（时间/地点/比分变化）自然覆盖成新版本；新出现的比赛正常加入。
    """
    existing_blocks = _extract_vevents(existing_ics_text)
    new_blocks = _extract_vevents(new_ics_text)

    refreshed_prefixes = tuple(f"kpl-{sid.lower()}" for sid in refreshed_season_ids)
    kept_existing = {
        uid: block
        for uid, block in existing_blocks.items()
        if not uid.startswith(refreshed_prefixes)
    }
    merged = {**kept_existing, **new_blocks}

    ordered_blocks = sorted(merged.values(), key=_vevent_sort_key)
    header, _, _ = new_ics_text.partition("BEGIN:VEVENT")
    return header + "".join(ordered_blocks) + "END:VCALENDAR\r\n"
