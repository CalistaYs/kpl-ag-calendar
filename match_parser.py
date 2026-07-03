#!/usr/bin/env python3
"""把 TGA 官方接口返回的原始比赛记录，转成日历生成需要的统一字段。"""
import datetime as dt

AG_NAME = "成都AG超玩会"
AG_SHORT_NAME = "AG"

# 用于生成不带城市前缀的队伍简称（如"成都AG超玩会"→"AG超玩会"，"重庆狼队"→"狼队"）。
# 这里维护的是"城市名"而不是逐支队伍的映射表，新战队只要用的是这些城市前缀就自动
# 适配；真正需要偶尔维护的只有下面的 KNOWN_TEAMS（用于识别到陌生队名时打印警告）。
CITY_PREFIXES = [
    "成都", "重庆", "北京", "上海", "广州", "武汉", "佛山", "济南",
    "苏州", "西安", "长沙", "南京", "南通", "杭州", "深圳",
]

# 已知战队名单，仅用于校验/告警：官方接口里出现不在这份名单里的队名
# （改名、扩军、缩编等）只会打印警告、不会中断更新——因为是否属于 AG 的比赛
# 已经由官方字段（hname/gname 是否等于 AG_NAME）直接判断，不依赖这份名单。
# 看到告警就把新队名加进来即可，遗漏也不会导致比赛被漏掉。
KNOWN_TEAMS = {
    "成都AG超玩会", "KSG", "重庆狼队", "深圳DYG", "济南RW侠", "WST", "SYG",
    "北京JDG", "广州TTG", "长沙TES.A", "西安WE", "佛山DRG", "北京WB",
    "杭州LGD.NBW", "武汉eStarPro", "上海EDG.M", "南通Hero久竞", "上海RNG.M",
}

# match_state 取值（与 pvp.qq.com 官网页面模板 esports_index.js 中的判断一致）：
# 1=未开始 3=进行中 4=已结束。只有 4 才认为比分是"官方最终比分"。
MATCH_STATE_FINISHED = 4


def short_team_name(name):
    """去掉城市前缀，返回队伍简称；不认识的前缀原样返回，不猜测、不截断。"""
    for city in CITY_PREFIXES:
        if name.startswith(city) and name != city:
            return name[len(city):]
    return name


def opponent_of(match):
    return match["away"] if match["home"] == AG_NAME else match["home"]


class ParseWarning(Exception):
    """单条记录本身有问题（缺字段/时间格式非法），跳过这一条，不影响其它比赛。"""


def normalize_match(raw):
    """把官方接口的一条原始记录转成内部统一字典。

    字段名直接对应官方语义（host=表格里排在前面的一方，guest=另一方），
    不假设 AG 一定在哪一侧，也不假设字段顺序，全部按官方给的字段名读取。
    """
    scheduleid = raw.get("scheduleid")
    match_time_str = raw.get("match_time")  # 形如 "2026-07-03 20:00:00"，北京时间
    home = raw.get("hname")
    away = raw.get("gname")
    if not scheduleid or not match_time_str or not home or not away:
        raise ParseWarning(f"比赛记录缺少必要字段，跳过：{raw}")
    try:
        start = dt.datetime.strptime(match_time_str, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ParseWarning(f"开赛时间格式无法解析（{match_time_str}），跳过：{exc}") from exc

    match_state = raw.get("match_state")
    home_score = raw.get("host_score")
    away_score = raw.get("guest_score")
    finished = (
        match_state == MATCH_STATE_FINISHED
        and home_score is not None
        and away_score is not None
    )

    season_label = (raw.get("season") or "").replace("KPL", "")  # "2026年KPL夏季赛" -> "2026年夏季赛"

    return {
        "scheduleid": scheduleid,
        "start": start,
        "home": home,
        "away": away,
        "home_score": home_score if finished else None,
        "away_score": away_score if finished else None,
        "match_state": match_state,
        "location": (raw.get("region") or "").strip(),
        "stage_name": (raw.get("stage_name") or "").strip(),
        "season_label": season_label,
        "bo_total": raw.get("bo_total"),
    }


def is_ag_match(match):
    return match["home"] == AG_NAME or match["away"] == AG_NAME


def parse_matches(raw_matches, warn=print):
    """解析全部原始记录，只保留 AG 参赛的场次；单条记录异常只警告不中断整体。"""
    matches = []
    for raw in raw_matches:
        try:
            match = normalize_match(raw)
        except ParseWarning as exc:
            warn(f"[WARN] {exc}")
            continue
        if not is_ag_match(match):
            continue
        for team in (match["home"], match["away"]):
            if team not in KNOWN_TEAMS:
                warn(
                    f"[WARN] 未知战队名：{team}（scheduleid={match['scheduleid']}），"
                    f"简称显示可能不够准确，请在 match_parser.py 补充 "
                    f"KNOWN_TEAMS / CITY_PREFIXES"
                )
        matches.append(match)
    matches.sort(key=lambda m: m["start"])
    return matches
