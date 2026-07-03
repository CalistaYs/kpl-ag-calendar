#!/usr/bin/env python3
import datetime as dt
import html
import re
import urllib.request

DEFAULT_START_TIME = "20:00"
DEFAULT_DURATION_HOURS = 3
ALARM_OFFSETS = ["-PT1H", "-PT30M"]
# 联赛总览页：始终存在，信息框里有一行“当前赛季、赛事或届次”并链接到当前赛季页面。
# 每次运行都先读这个页面找到当前赛季链接，这样春/夏季赛切换、跨年份都不需要改代码。
MAIN_WIKI_URL = "https://zh.wikipedia.org/wiki/%E7%8E%8B%E8%80%85%E8%8D%A3%E8%80%80%E8%81%8C%E4%B8%9A%E8%81%94%E8%B5%9B"
# 仅作为“当前赛季”自动发现失败时的最后兜底，指向已知能正常解析的一个赛季页面。
FALLBACK_WIKI_URL = "https://zh.wikipedia.org/wiki/%E7%8E%8B%E8%80%85%E8%8D%A3%E8%80%80%E8%81%8C%E4%B8%9A%E8%81%94%E8%B5%9B2026%E5%B9%B4%E5%A4%8F%E5%AD%A3%E8%B5%9B"
FALLBACK_YEAR = 2026
FALLBACK_SEASON_LABEL = "2026年夏季赛"
KPL_URL = "https://pvp.qq.com/match/kpl/"
AG_NAMES = {"成都AG超玩会", "成都AG超玩會"}
TEAMS = [
    "成都AG超玩会", "成都AG超玩會", "KSG", "重庆狼队", "重慶狼隊", "深圳DYG", "济南RW侠", "濟南RW俠",
    "WST", "SYG", "北京JDG", "广州TTG", "廣州TTG", "长沙TES.A", "長沙TES.A", "西安WE",
    "佛山DRG", "北京WB", "杭州LGD.NBW", "武汉eStarPro", "武漢eStarPro", "上海EDG.M",
    "南通Hero久竞", "南通Hero久競", "上海RNG.M"
]
# 各战队队名前缀所在城市，用于生成去掉城市前缀后的简称（如“成都AG超玩会”→“AG超玩会”）。
CITY_PREFIXES = [
    "成都", "重庆", "北京", "上海", "广州", "武汉", "佛山", "济南",
    "苏州", "西安", "长沙", "南京", "南通", "杭州", "深圳",
]
# 2026年夏季赛的已知数据，仅在“当前赛季”自动发现失败、且发现结果仍是这一季时用作兜底。
# 不代表未来赛季的赛程，未来赛季应完全依赖自动抓取。
# 字段：日期, 主场, 客场, 主场比分, 客场比分, 比赛地点, 本赛季双方第几次交手
# Wikipedia 赛程表没有地点列，因此地点留空；比分未知（未开赛）时用 None，不编造。
FALLBACK_EVENTS = [
    ("20260619", "成都AG超玩会", "KSG", 3, 1, "", 1),
    ("20260621", "成都AG超玩会", "WST", 3, 1, "", 1),
    ("20260627", "成都AG超玩会", "济南RW侠", 0, 3, "", 1),
    ("20260703", "成都AG超玩会", "重庆狼队", None, None, "", 1),
    ("20260705", "成都AG超玩会", "深圳DYG", None, None, "", 1),
]


def clean_token(text):
    text = html.unescape(text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
CELL_SPLIT_RE = re.compile(r"</t[dh]>", re.I)


def extract_rows(page_html):
    """按 <tr>/<td> 的真实行列结构解析赛程表，而不是把整页拍平成一条 token 流。

    之前的实现会把所有单元格拼成一条线性 token 序列，导致"往后扫描 N 个 token 找比分"
    的逻辑会跨越表格行边界，把积分榜、名单等不相关表格里的数字/队名误当成比赛数据。
    按行处理后，每场比赛只在自己所在的 <tr> 内查找比分和对手，不会串到别的行/表格。
    """
    text = re.sub(r"<script[\s\S]*?</script>", " ", page_html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    rows = []
    for row_html in ROW_RE.findall(text):
        cells = []
        for cell_html in CELL_SPLIT_RE.split(row_html):
            cell_text = clean_token(re.sub(r"<[^>]+>", " ", cell_html))
            if cell_text:
                cells.append(cell_text)
        if cells:
            rows.append(cells)
    return rows


def is_team(token):
    return token in TEAMS


def norm_team(team):
    return (
        team.replace("會", "会")
        .replace("重慶", "重庆")
        .replace("濟南", "济南")
        .replace("武漢", "武汉")
        .replace("廣州", "广州")
        .replace("長沙", "长沙")
        .replace("競", "竞")
        .replace("隊", "队")
    )


def short_team_name(team):
    """去掉城市前缀，返回队伍简称，例如 成都AG超玩会 -> AG超玩会，重庆狼队 -> 狼队。"""
    name = norm_team(team)
    for city in CITY_PREFIXES:
        if name.startswith(city) and name != city:
            return name[len(city):]
    return name


# 日历标题里 AG 一方固定显示为 "AG"（而不是 "AG超玩会"）。
AG_SHORT_NAME = "AG"


def opponent_for(home, away):
    return away if home == "成都AG超玩会" else home


DATE_RE = re.compile(r"^(\d{1,2})月(\d{1,2})日$")
SCORE_RE = re.compile(r"^\d+$")


def parse_events(rows, year):
    """按行解析赛程表。每行要么是跨列的日期行（1 个单元格），要么是一场比赛：
    [队伍1, 队伍2]（未开赛，比分单元格为空）或 [队伍1, 比分1, 比分2, 队伍2]（已完赛）。
    不符合这两种形状的行（积分榜、名单等其他表格）会被直接跳过，不会被误当成比赛。
    """
    events = {}
    pair_occurrence = {}
    current_date = None
    for row in rows:
        if len(row) == 1:
            date_match = DATE_RE.match(row[0])
            if date_match:
                month, day = map(int, date_match.groups())
                current_date = f"{year}{month:02d}{day:02d}"
            continue
        if not current_date:
            continue
        if len(row) == 2 and is_team(row[0]) and is_team(row[1]):
            team1, team2 = row
            score1 = score2 = None
        elif (
            len(row) == 4
            and is_team(row[0])
            and SCORE_RE.match(row[1])
            and SCORE_RE.match(row[2])
            and is_team(row[3])
        ):
            team1, score1, score2, team2 = row
        else:
            continue
        if team1 not in AG_NAMES and team2 not in AG_NAMES:
            continue
        home = norm_team(team1)
        away = norm_team(team2)
        key = (current_date, home, away)
        if key in events:
            continue
        # Wikipedia 赛程表没有地点列，暂时留空；不编造地点。
        location = ""
        home_score = int(score1) if score1 is not None else None
        away_score = int(score2) if score2 is not None else None
        pair_key = tuple(sorted([home, away]))
        pair_occurrence[pair_key] = pair_occurrence.get(pair_key, 0) + 1
        events[key] = (
            current_date, home, away, home_score, away_score, location, pair_occurrence[pair_key]
        )
    return sorted(events.values(), key=lambda x: (x[0], x[1], x[2]))


def ics_escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def make_uid(season_label, home, away, occurrence):
    # 特意不用日期做 UID：这样比赛延期改期只更新 DTSTART，不会在订阅日历里变成新事件。
    # occurrence 用来区分本赛季双方多次交手（如常规赛+季后赛再战）。
    pair = "-".join(sorted([home, away]))
    slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{season_label}-{pair}-{occurrence}").strip("-").lower()
    return f"kpl-ag-{slug}@calistays.github"


def event_times(date):
    start_hour, start_minute = map(int, DEFAULT_START_TIME.split(":"))
    start = dt.datetime.strptime(date, "%Y%m%d").replace(hour=start_hour, minute=start_minute)
    end = start + dt.timedelta(hours=DEFAULT_DURATION_HOURS)
    return start.strftime("%Y%m%dT%H%M%S"), end.strftime("%Y%m%dT%H%M%S")


def build_calendar(events, season_label):
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
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
    for date, home, away, home_score, away_score, location, occurrence in events:
        start, end = event_times(date)
        opponent_short = short_team_name(opponent_for(home, away))
        summary = f"{AG_SHORT_NAME} VS {opponent_short}"

        desc_lines = [
            f"KPL {season_label}。",
            f"{home} vs {away}。",
            f"开赛时间：{DEFAULT_START_TIME}（北京时间 GMT+8，具体时间以官方公布为准）。",
        ]
        if location:
            desc_lines.append(f"比赛地点：{location}")
        if home_score is not None and away_score is not None:
            is_ag_home = home in AG_NAMES
            ag_score = home_score if is_ag_home else away_score
            opp_score = away_score if is_ag_home else home_score
            desc_lines.append(f"比赛结果：{AG_SHORT_NAME} {ag_score}:{opp_score} {opponent_short}")
        desc_lines.append(f"官方观赛入口：{KPL_URL}")
        detail = "\n".join(desc_lines)

        alarm_description = f"{summary} 即将开始"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{make_uid(season_label, home, away, occurrence)}",
            f"DTSTAMP:{stamp}",
            f"SUMMARY:{ics_escape(summary)}",
            f"DTSTART;TZID=Asia/Shanghai:{start}",
            f"DTEND;TZID=Asia/Shanghai:{end}",
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


def fetch_page(url):
    req = urllib.request.Request(url, headers={"User-Agent": "kpl-ag-calendar/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


CURRENT_SEASON_RE = re.compile(
    r'当前赛季、赛事或届次：</b>\s*<br\s*/?>\s*<a href="([^"]+)"[^>]*title="([^"]+)"'
)


def discover_current_season():
    """从联赛总览页解析出"当前赛季"链接，让脚本自动跟随最新赛季（春/夏季赛、跨年份均适用）。

    返回 (wiki_url, year, season_label)；解析失败（页面结构变化、网络问题等）时返回 None，
    调用方会退回到 FALLBACK_WIKI_URL。
    """
    try:
        page = fetch_page(MAIN_WIKI_URL)
    except Exception:
        return None
    match = CURRENT_SEASON_RE.search(page)
    if not match:
        return None
    href, title = match.group(1), html.unescape(match.group(2))
    year_match = re.search(r"(\d{4})年", title)
    if not year_match:
        return None
    year = int(year_match.group(1))
    season_label = title.replace("王者荣耀职业联赛", "")
    return f"https://zh.wikipedia.org{href}", year, season_label


def main():
    discovered = discover_current_season()
    if discovered:
        wiki_url, year, season_label = discovered
    else:
        wiki_url, year, season_label = FALLBACK_WIKI_URL, FALLBACK_YEAR, FALLBACK_SEASON_LABEL

    try:
        page = fetch_page(wiki_url)
        events = parse_events(extract_rows(page), year)
    except Exception:
        events = []

    if not events:
        if year != FALLBACK_YEAR or season_label != FALLBACK_SEASON_LABEL:
            # 新赛季暂时抓不到 AG 的比赛（例如赛程还未公布），不要用旧赛季的兜底数据
            # 冒充新赛季，保留现有 calendar.ics 不动即可。
            print(f"No AG matches found for {season_label}; leaving calendar.ics unchanged.")
            return
        events = FALLBACK_EVENTS

    with open("calendar.ics", "w", encoding="utf-8", newline="\n") as f:
        f.write(build_calendar(events, season_label))


if __name__ == "__main__":
    main()
