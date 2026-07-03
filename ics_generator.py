#!/usr/bin/env python3
"""把校验通过的比赛列表渲染成 RFC5545 格式的 .ics 文本，并支持跨赛季合并。"""
import datetime as dt
import re

from match_parser import AG_NAME, AG_SHORT_NAME, opponent_of, short_team_name

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


def make_uid(scheduleid):
    # scheduleid（如 KPL2026S2M3W3D3）是腾讯官方赛程系统里给这场比赛分配的稳定
    # ID，不会随开赛时间/地点/比分变化——延期、改期、更新比分都还是这一个 UID，
    # 比自己拼"日期+队名"更可靠，也不会和其它比赛冲突。
    slug = scheduleid.strip().lower()
    return f"kpl-{slug}@calistays.github"


def build_calendar(matches, dtstamp=None):
    stamp = (dtstamp or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CalistaYs//KPL AG Calendar//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:KPL 成都AG超玩会赛程",
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
        if m["stage_name"]:
            season_line += f" {m['stage_name']}"
        desc_lines = [
            f"{season_line}。",
            f"{m['home']} vs {m['away']}。",
            f"开赛时间：{start.strftime('%H:%M')}（北京时间 GMT+8，官方数据）。",
        ]
        if location:
            desc_lines.append(f"比赛地点：{location}")
        if m["home_score"] is not None and m["away_score"] is not None:
            is_ag_home = m["home"] == AG_NAME
            ag_score = m["home_score"] if is_ag_home else m["away_score"]
            opp_score = m["away_score"] if is_ag_home else m["home_score"]
            desc_lines.append(f"比赛结果：{AG_SHORT_NAME} {ag_score}:{opp_score} {opponent_short}")
        desc_lines.append(f"官方观赛入口：{KPL_URL}")
        detail = "\n".join(desc_lines)

        alarm_description = f"{summary} 即将开始"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{make_uid(m['scheduleid'])}",
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


def merge_calendars(existing_ics_text, new_ics_text, season_id):
    """把这次新抓到的比赛（new_ics_text，某一个赛季 season_id 的完整赛程）合并进
    已有日历（existing_ics_text）。

    - 不属于 season_id 的历史比赛（UID 里的 scheduleid 前缀是其它赛季）——也就是
      这次请求根本没有触及、已经切换过去的赛季——原样保留，不会因为跨赛季就从
      日历里消失；未被触及的历史事件也保留原有的 DTSTAMP，不会被当成"刚生成"。
    - 属于 season_id 的比赛，用这次抓到的结果完整替换旧版本：因为每次都是拉取
      "该赛季的全部比赛"（不是增量），所以这次没有出现的旧记录（比如被取消的
      比赛）应该跟着消失，不能残留成永远删不掉的僵尸事件；同 UID 有更新的（时间/
      地点/比分变化）自然覆盖成新版本；新出现的比赛正常加入。
    """
    existing_blocks = _extract_vevents(existing_ics_text)
    new_blocks = _extract_vevents(new_ics_text)

    season_uid_prefix = f"kpl-{season_id.lower()}"
    kept_existing = {
        uid: block
        for uid, block in existing_blocks.items()
        if not uid.startswith(season_uid_prefix)
    }
    merged = {**kept_existing, **new_blocks}

    ordered_blocks = sorted(merged.values(), key=_vevent_sort_key)
    header, _, _ = new_ics_text.partition("BEGIN:VEVENT")
    return header + "".join(ordered_blocks) + "END:VCALENDAR\r\n"
