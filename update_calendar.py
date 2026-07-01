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
FALLBACK_EVENTS = [
    ("20260619", "成都AG超玩会", "KSG", "已完赛：成都AG超玩会 3-1 KSG"),
    ("20260621", "成都AG超玩会", "WST", "已完赛：成都AG超玩会 3-1 WST"),
    ("20260627", "成都AG超玩会", "济南RW侠", "已完赛：成都AG超玩会 0-3 济南RW侠"),
    ("20260703", "成都AG超玩会", "重庆狼队", ""),
    ("20260705", "成都AG超玩会", "深圳DYG", ""),
]


def clean_token(text):
    text = html.unescape(text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_tokens(page_html):
    text = re.sub(r"<script[\s\S]*?</script>", " ", page_html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"</(?:td|th|tr|p|li|h[1-6]|div|br)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    tokens = [clean_token(t) for t in re.split(r"[\n|]+", text)]
    return [t for t in tokens if t]


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


def parse_events(tokens, year):
    events = {}
    current_date = None
    date_re = re.compile(r"^(\d{1,2})月(\d{1,2})日$")
    for i, token in enumerate(tokens):
        date_match = date_re.match(token)
        if date_match:
            month, day = map(int, date_match.groups())
            current_date = f"{year}{month:02d}{day:02d}"
            continue
        if not current_date or not is_team(token):
            continue
        team1 = token
        score1 = score2 = None
        team2 = None
        for j in range(i + 1, min(i + 8, len(tokens))):
            probe = tokens[j]
            if re.fullmatch(r"\d+", probe):
                if score1 is None:
                    score1 = probe
                elif score2 is None:
                    score2 = probe
                continue
            if is_team(probe):
                team2 = probe
                break
        if not team2:
            continue
        if team1 not in AG_NAMES and team2 not in AG_NAMES:
            continue
        home = norm_team(team1)
        away = norm_team(team2)
        note = f"已完赛：{home} {score1}-{score2} {away}" if score1 is not None and score2 is not None else ""
        key = (current_date, home, away)
        events[key] = (current_date, home, away, note)
    return sorted(events.values(), key=lambda x: (x[0], x[1], x[2]))


def ics_escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def make_uid(date, home, away):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{date}-{home}-{away}").strip("-").lower()
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
    for date, home, away, note in events:
        start, end = event_times(date)
        opponent_short = short_team_name(opponent_for(home, away))
        summary = f"{AG_SHORT_NAME} VS {opponent_short}"
        original_title = f"KPL：{home} vs {away}"
        detail = (
            f"原标题：{original_title}。开赛时间：{DEFAULT_START_TIME}（北京时间 GMT+8，"
            f"具体时间以官方公布为准）。KPL {season_label}。"
        )
        if note:
            detail += note + "。"
        detail += f"观赛入口：{KPL_URL}"
        alarm_description = f"{summary} 即将开始"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{make_uid(date, home, away)}",
            f"DTSTAMP:{stamp}",
            f"SUMMARY:{ics_escape(summary)}",
            f"DTSTART;TZID=Asia/Shanghai:{start}",
            f"DTEND;TZID=Asia/Shanghai:{end}",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            f"URL:{KPL_URL}",
            f"DESCRIPTION:{ics_escape(detail)}",
        ])
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
        events = parse_events(extract_tokens(page), year)
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
