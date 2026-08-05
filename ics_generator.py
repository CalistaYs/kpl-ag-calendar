#!/usr/bin/env python3
"""把校验通过的比赛列表渲染成 RFC5545 格式的 .ics 文本，并支持跨赛事/跨赛季合并。"""
import datetime as dt
import re

from match_parser import (
    AG_SHORT_NAME,
    MATCH_STATE_FINISHED,
    is_legal_final_score,
    is_target_team,
    opponent_of,
    short_team_name,
)

DEFAULT_DURATION_HOURS = 3
ALARM_OFFSETS = ("-PT1H", "-PT30M")
KPL_URL = "https://pvp.qq.com/match/kpl/"
BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))


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


def _resolve_final_score_state(match, existing_state):
    """决定这场比赛在这次生成里，DESCRIPTION 该不该显示比分、显示哪个比分，以及
    X-MATCH-FINAL-SCORE 该不该标成 YES。

    "已完赛 + 双方比分都存在"（match['home_score']/['away_score'] 非 None，这个
    条件在 match_parser.normalize_match 里已经算好）只代表官方状态和比分字段都
    有值，不代表这个比分就是可信的最终赛果——历史上出现过官方状态已经是"已完赛"、
    但比分其实还是中间比分的情况（比如 BO5 显示 1:0）。这里额外加两层保护：

    1. 赛制合法性：用 match_parser.is_legal_final_score() 按 bo_total 校验比分是否
       够得上"胜方达到获胜局数、负方明确更少"。明确不合法时，整场比赛按"暂时没有
       可信比分"处理——不显示、不确认，但也不会抹掉之前已经确认过的合法结果
       （避免一次异常读数把已经稳定的赛果打回原形）。赛制未知（bo_total 缺失）时
       无法判断合法性，按"先展示、但不能单凭这一次就确认"处理。
    2. 连续两次一致确认：赛制合法的比分第一次出现时，只当"候选比分"看待，写入
       比分但 X-MATCH-FINAL-SCORE 保持 NO；下一次读到完全相同的合法比分时才真正
       确认为 YES。候选比分中途变化（官方修正）会重新从"候选、NO"开始计数，不会
       把中间状态误当最终赛果。

    existing_state 是 calendar.ics 里这场比赛（按 UID）当前记录的
    {"score": (home,away) 或 None, "final": bool}，没有记录时是
    {"score": None, "final": False}（比如这是一场全新的比赛，或者 calendar.ics
    还没有这两个字段）。

    返回 (display_home_score, display_away_score, is_final)；display_* 是 None
    时表示这场比赛现在不应该显示"比赛结果"这一行。
    """
    home_score, away_score = match["home_score"], match["away_score"]
    if home_score is None or away_score is None:
        return None, None, False

    bo_legal = is_legal_final_score(home_score, away_score, match.get("bo_total"))
    if bo_legal is False:
        # 明确不合法：当成"这次没有可信比分"，保留（不清空）已有的候选/确认状态。
        if existing_state["score"] is not None:
            old_home, old_away = existing_state["score"]
            return old_home, old_away, existing_state["final"]
        return None, None, False

    candidate = (home_score, away_score)
    already_confirmed_same = existing_state["final"] and existing_state["score"] == candidate
    newly_confirmable = (
        bo_legal is True
        and not existing_state["final"]
        and existing_state["score"] == candidate
    )
    if already_confirmed_same or newly_confirmable:
        return home_score, away_score, True

    # 第一次看到这个候选比分（不管 bo_legal 是 True 还是"未知"），先展示、不确认。
    return home_score, away_score, False


def _format_bo_label(bo_total):
    """DESCRIPTION 里"赛制："那一行的取值。只认官方接口给的 bo_total 字段本身，
    不根据赛事名称/阶段/经验猜测——bo_total 不是合法正整数（缺失、非数字、<=0）
    时一律显示"未知"，不编造一个可能错误的赛制。
    """
    if isinstance(bo_total, int) and not isinstance(bo_total, bool) and bo_total > 0:
        return f"BO{bo_total}"
    return "未知"


def _status_label(match_state):
    """DESCRIPTION 里"状态："那一行的取值，直接对应官方 match_state
    （1=未开始 3=进行中 4=已结束）；其它/缺失值保守按"未开赛"处理，不假装
    已经知道比赛进行到哪一步。"""
    if match_state == MATCH_STATE_FINISHED:
        return "已完赛"
    if match_state == 3:
        return "进行中"
    return "未开赛"


def build_calendar(matches, dtstamp=None, existing_final_states=None):
    """existing_final_states：{uid: {"score": (home,away) 或 None, "final": bool}}，
    来自上一次 calendar.ics 里同一场比赛（按 UID）的记录，用于两次确认判断
    （见 _resolve_final_score_state）。不传时视为没有任何历史记录，所有比分都从
    "候选、未确认"开始——这正是从旧版 calendar.ics（还没有 X-MATCH-* 字段）迁移
    过来时的安全默认值。
    """
    existing_final_states = existing_final_states or {}
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
        uid = make_uid(m["season_id"], m["scheduleid"])

        existing_state = existing_final_states.get(uid) or {"score": None, "final": False}
        display_home, display_away, is_final = _resolve_final_score_state(m, existing_state)

        status_label = _status_label(m["match_state"])
        has_display_score = display_home is not None and display_away is not None

        desc_lines = [
            f"赛事：{m['season_label']}" if m["season_label"] else "赛事：未知",
            f"阶段：{m['stage_label']}" if m["stage_label"] else "阶段：未知",
            f"赛制：{_format_bo_label(m.get('bo_total'))}",
            "",
            "对阵：",
            f"{m['home']} vs {m['away']}",
            "",
            "状态：",
            status_label,
            "",
        ]
        if status_label == "已完赛" and has_display_score:
            is_ag_home = is_target_team(m["home"])
            ag_score = display_home if is_ag_home else display_away
            opp_score = display_away if is_ag_home else display_home
            desc_lines.append("最终比分：")
            desc_lines.append(f"{AG_SHORT_NAME} {ag_score} : {opp_score} {opponent_short}")
        else:
            time_label = "预计开赛" if status_label == "未开赛" else "开赛时间"
            desc_lines.append(f"{time_label}：")
            desc_lines.append(f"{start.strftime('%Y-%m-%d %H:%M')}（北京时间 GMT+8）")
        desc_lines.append("")
        if location:
            desc_lines.append("比赛地点：")
            desc_lines.append(location)
            desc_lines.append("")
        desc_lines.append("官方观赛：")
        desc_lines.append(KPL_URL)
        detail = "\n".join(desc_lines)

        alarm_description = f"{summary} 即将开始"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
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
        # 以下两个 X- 属性是自定义扩展（RFC5545 允许，标准日历软件会安全忽略未知的
        # X- 属性，不影响导入），供 smart_update.py 的高频调度识别"这场比赛属于
        # 哪个赛事"和"上次记录的候选/确认比分"，不需要另外解析 UID 或 DESCRIPTION。
        lines.append(f"X-SEASON-ID:{ics_escape(m['season_id'])}")
        if display_home is not None and display_away is not None:
            lines.append(f"X-MATCH-SCORE:{display_home}:{display_away}")
            lines.append(f"X-MATCH-FINAL-SCORE:{'YES' if is_final else 'NO'}")
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
_DTSTAMP_LINE_RE = re.compile(r"^DTSTAMP:.*\r?\n", re.M)


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


def _strip_dtstamp(block):
    return _DTSTAMP_LINE_RE.sub("", block, count=1)


def extract_uids(ics_text):
    """返回一段 ICS 文本里所有 VEVENT 的 UID 集合，供调用方统计更新/新增/移除数量。"""
    return set(_extract_vevents(ics_text).keys())


def merge_calendars(existing_ics_text, new_ics_text, refreshed_season_ids, preserve_unchanged_dtstamp=False):
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
    - preserve_unchanged_dtstamp=True 时，如果某个 UID 新旧两版内容除了 DTSTAMP
      之外完全一样（DTSTART/DTEND/SUMMARY/DESCRIPTION/LOCATION/X-SEASON-ID/
      X-MATCH-FINAL-SCORE/VALARM 等都没变），就保留旧的事件块（连带旧 DTSTAMP），
      避免高频模式（每 30 分钟跑一次）因为"什么实质内容都没变、只是重新生成了
      一遍"而每次都产生一条 git diff。默认关闭，完整扫描路径的行为完全不受影响。
    """
    existing_blocks = _extract_vevents(existing_ics_text)
    new_blocks = _extract_vevents(new_ics_text)

    refreshed_prefixes = tuple(f"kpl-{sid.lower()}" for sid in refreshed_season_ids)
    kept_existing = {
        uid: block
        for uid, block in existing_blocks.items()
        if not uid.startswith(refreshed_prefixes)
    }

    if preserve_unchanged_dtstamp:
        for uid, new_block in list(new_blocks.items()):
            old_block = existing_blocks.get(uid)
            if old_block is not None and _strip_dtstamp(old_block) == _strip_dtstamp(new_block):
                new_blocks[uid] = old_block

    merged = {**kept_existing, **new_blocks}

    ordered_blocks = sorted(merged.values(), key=_vevent_sort_key)
    header, _, _ = new_ics_text.partition("BEGIN:VEVENT")
    return header + "".join(ordered_blocks) + "END:VCALENDAR\r\n"


_FOLD_RE = re.compile(r"\r?\n[ \t]")
_DTSTART_TZID_RE = re.compile(r"^DTSTART;TZID=Asia/Shanghai:(\d{8}T\d{6})\r?$", re.M)
_DTSTART_UTC_RE = re.compile(r"^DTSTART:(\d{8}T\d{6})Z\r?$", re.M)
_XSEASON_RE = re.compile(r"^X-SEASON-ID:(.+?)\r?$", re.M)
_XFINAL_RE = re.compile(r"^X-MATCH-FINAL-SCORE:(.+?)\r?$", re.M)
_XSCORE_RE = re.compile(r"^X-MATCH-SCORE:(\d+):(\d+)\r?$", re.M)


def _unfold(text):
    """RFC5545 折行展开：续行以单个空格或 Tab 开头，展开时把换行和那一个前导
    空白字符一起删掉。我们自己生成的 calendar.ics 目前不折行，这里是为了兼容
    可能被别的工具/手工编辑过的文件，不假设输入一定是自己写的格式。
    """
    return _FOLD_RE.sub("", text)


def parse_calendar_events(ics_text):
    """把 calendar.ics 解析成轻量事件列表，供 smart_update.py 判断"比赛窗口"、
    以及两次确认机制读取"上一次记录的候选/确认比分"用。

    返回 [{"uid", "season_id", "start", "final", "score"}, ...]；
    - start 是带时区（Asia/Shanghai 或 UTC）的 datetime，不会返回不带时区的裸时间。
    - season_id 在事件缺少 X-SEASON-ID 时是 None（不报错）。
    - final 在事件缺少 X-MATCH-FINAL-SCORE、或值不是 "YES" 时是 False。
    - score 在事件缺少 X-MATCH-SCORE、或格式不对时是 None。

    对每个 VEVENT：只要解析不出 UID，或者 DTSTART 既不是
    "TZID=Asia/Shanghai" 本地时间、也不是纯 UTC（结尾 Z），就整条跳过——不会
    拿一个猜出来的时区去瞎比较；调用方看到"某场比赛解析不出来"不代表整个文件
    坏了，是否需要因此回退到完整扫描由调用方根据"解析出来的事件数量"决定。
    """
    text = _unfold(ics_text)
    events = []
    for block in _VEVENT_RE.findall(text):
        uid_match = _UID_RE.search(block)
        if not uid_match:
            continue

        start = None
        tzid_match = _DTSTART_TZID_RE.search(block)
        if tzid_match:
            start = dt.datetime.strptime(tzid_match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=BEIJING_TZ)
        else:
            utc_match = _DTSTART_UTC_RE.search(block)
            if utc_match:
                start = dt.datetime.strptime(utc_match.group(1), "%Y%m%dT%H%M%S").replace(
                    tzinfo=dt.timezone.utc
                )
        if start is None:
            continue

        season_match = _XSEASON_RE.search(block)
        final_match = _XFINAL_RE.search(block)
        score_match = _XSCORE_RE.search(block)

        events.append({
            "uid": uid_match.group(1).strip(),
            "season_id": season_match.group(1).strip() if season_match else None,
            "start": start,
            "final": bool(final_match) and final_match.group(1).strip().upper() == "YES",
            "score": (int(score_match.group(1)), int(score_match.group(2))) if score_match else None,
        })
    return events
