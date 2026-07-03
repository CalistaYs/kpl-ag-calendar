#!/usr/bin/env python3
"""把 TGA 官方接口返回的原始比赛记录，转成日历生成需要的统一字段；负责判断
一场比赛是否跟目标战队（默认成都AG超玩会，也认得出国际赛事里的别名）有关。
"""
import datetime as dt
import os
import re

AG_SHORT_NAME = "AG"

DEFAULT_TARGET_TEAM = "成都AG超玩会"

# 默认别名清单：中文全称/简称 + 国际赛事里出现过的英文命名变体。用真实接口数据
# 验证过——"All Gamers Global" 是 2024 电竞世界杯（EWC2024）里 AG 的参赛队名，
# 跟中文全称完全不一样，必须靠别名/模糊匹配才能认出来是同一支队伍。
DEFAULT_TARGET_ALIASES = [
    "成都AG超玩会", "成都 AG 超玩会", "AG超玩会", "成都AG", "AG",
    "AG.AL", "AG AL", "AG_AL", "All Gamers", "All Gamers Global",
]

TARGET_TEAM_ENV = "TARGET_TEAM"
TARGET_TEAM_ALIASES_ENV = "TARGET_TEAM_ALIASES"


def _load_target_and_aliases():
    target = os.environ.get(TARGET_TEAM_ENV, "").strip() or DEFAULT_TARGET_TEAM
    raw_aliases = os.environ.get(TARGET_TEAM_ALIASES_ENV, "").strip()
    if raw_aliases:
        aliases = [a.strip() for a in raw_aliases.split(",") if a.strip()]
    else:
        aliases = list(DEFAULT_TARGET_ALIASES)
    if target not in aliases:
        aliases.append(target)
    return target, aliases


TARGET_TEAM, TARGET_ALIASES = _load_target_and_aliases()

# 用于生成不带城市前缀的队伍简称（如"成都AG超玩会"→"AG超玩会"，"重庆狼队"→"狼队"）。
# 国际赛事的对手（"Weibo Gaming"、"Team Falcons"……）不带这些前缀，原样返回。
CITY_PREFIXES = [
    "成都", "重庆", "北京", "上海", "广州", "武汉", "佛山", "济南",
    "苏州", "西安", "长沙", "南京", "南通", "杭州", "深圳",
]

# 已知国内战队名单，仅用于给"陌生队名"打印警告（改名/扩军/缩编时）；只在国内
# 常规赛/总决赛（scheduleid 形如 KPL{年}S{n}）里检查——国际赛事的对手阵容变化
# 很大、也没有维护完整名单的必要，不在这份清单里不代表数据有问题。
KNOWN_TEAMS = {
    "成都AG超玩会", "KSG", "重庆狼队", "深圳DYG", "济南RW侠", "WST", "SYG",
    "北京JDG", "广州TTG", "长沙TES.A", "西安WE", "佛山DRG", "北京WB",
    "杭州LGD.NBW", "武汉eStarPro", "上海EDG.M", "南通Hero久竞", "上海RNG.M",
}

_DOMESTIC_SEASON_RE = re.compile(r"^KPL\d{4}S\d+", re.I)

# match_state 取值（与 pvp.qq.com 官网页面模板 esports_index.js 中的判断一致）：
# 1=未开始 3=进行中 4=已结束。只有 4 才认为比分是"官方最终比分"。
MATCH_STATE_FINISHED = 4

_SEPARATOR_RE = re.compile(r"[\s._-]+")


def _normalize(name):
    """小写 + 去掉空格/点(.)/下划线(_)/短横线(-)，用于中文名称、较长英文短语的包含匹配。"""
    return _SEPARATOR_RE.sub("", name.lower())


def _words(name):
    return [w for w in _SEPARATOR_RE.split(name.lower()) if w]


def _contains_cjk(text):
    return any("一" <= ch <= "鿿" for ch in text)


def is_target_team(name):
    """判断 name 是否指向目标战队（含大小写/空格/点/下划线/短横线变体、国际赛事别名）。

    - 中文别名、或归一化后长度 >= 5 的英文短语（如 "All Gamers"、"All Gamers Global"）：
      用归一化后的包含匹配。
    - 短英文别名（如 "AG"）：只用整词匹配（按空格/点/下划线/短横线分词后逐词比较），
      不用 contains，避免命中 package/stage/magic 这类普通单词里恰好带着 "ag" 的情况。
      多词的短别名（"AG.AL"/"AG AL"/"AG_AL"，归一化后都是 "agal"）要求整体完全相等。
    """
    if not name:
        return False
    norm_name = _normalize(name)
    name_words = _words(name)
    for alias in TARGET_ALIASES:
        norm_alias = _normalize(alias)
        if not norm_alias:
            continue
        if _contains_cjk(alias) or len(norm_alias) >= 5:
            if norm_alias in norm_name:
                return True
        else:
            alias_words = _words(alias)
            if len(alias_words) == 1:
                if alias_words[0] in name_words:
                    return True
            elif norm_alias == norm_name:
                return True
    return False


def short_team_name(name):
    """去掉城市前缀，返回队伍简称；不认识的前缀（含国际赛事对手）原样返回。"""
    for city in CITY_PREFIXES:
        if name.startswith(city) and name != city:
            return name[len(city):]
    return name


def opponent_of(match):
    return match["away"] if is_target_team(match["home"]) else match["home"]


_GROUP_LETTER_RE = re.compile(r"^[A-Za-z]$")


def extract_stage_label(raw):
    """从官方接口的原始字段里，尽量还原这场比赛所在的赛事阶段，用于日历备注里的
    "阶段：" 一行。只使用真实存在的字段，不猜测、不编造更细的阶段划分。

    检查过官方接口实际返回的全部字段：只有 stage（阶段代号，如 "cgs1"）、
    stage_name（阶段中文名，如 "常规赛第一轮"）、host_group/guest_group（单字母
    组别，如 "S"/"A"/"B"）；没有 round / round_name / group_name / match_name /
    match_title / desc 这些字段，季后赛也没有"胜者组/败者组/第几轮"的独立字段——
    stage_name 对季后赛只给到"季后赛"这一级（决赛/卡位赛是单独的 stage_name）。
    所以这里能做到的是：

    - 常规赛（stage_name 里带"常规赛"）：如果能读到目标战队一侧的组别（单个字母，
      比如 S/A/B），拼成"常规赛 S组"这样的形式；读不到组别就只返回"常规赛"。
    - 其它阶段（季后赛/决赛/卡位赛/小组赛/淘汰赛/半决赛/单败淘汰赛/双败淘汰赛/
      表演赛……）：原样返回官方给出的 stage_name，不做拆分——拆成"胜者组第一轮"
      这种更细的说法在当前接口字段里没有依据，不编造。
    - stage_name 本身缺失/为空时返回 None，调用方不应该显示"阶段："这一行。
    """
    stage_name = (raw.get("stage_name") or "").strip()
    if not stage_name:
        return None
    if "常规赛" in stage_name:
        is_ag_home = is_target_team(raw.get("hname"))
        group = raw.get("host_group") if is_ag_home else raw.get("guest_group")
        if isinstance(group, str) and _GROUP_LETTER_RE.match(group):
            return f"常规赛 {group.upper()}组"
        return "常规赛"
    return stage_name


class ParseWarning(Exception):
    """单条记录本身有问题（缺字段/时间格式非法），跳过这一条，不影响其它比赛。"""


def normalize_match(raw):
    """把官方接口的一条原始记录转成内部统一字典。

    字段名直接对应官方语义（host=表格里排在前面的一方，guest=另一方），
    不假设目标战队一定在哪一侧，也不假设字段顺序，全部按官方给的字段名读取。
    raw["_season_id"] 由调用方（fetch.scan_all_seasons 的结果）预先标记好，
    用来在 ics_generator 里拼 UID、以及做"这条比赛属于哪个赛事"的归属判断。
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
        "season_id": raw.get("_season_id", ""),
        "start": start,
        "home": home,
        "away": away,
        "home_score": home_score if finished else None,
        "away_score": away_score if finished else None,
        "match_state": match_state,
        "location": (raw.get("region") or "").strip(),
        "stage_label": extract_stage_label(raw),
        "season_label": season_label,
        "bo_total": raw.get("bo_total"),
    }


def is_ag_match(match):
    return is_target_team(match["home"]) or is_target_team(match["away"])


def list_teams(raw_matches):
    """从一批原始比赛记录（官方接口返回的 hname/gname）里提取出现过的全部队伍名，
    标记每一个是否被识别为目标战队。返回按名称排序的 [(队名, 是否命中), ...]。

    用途：万一目标战队以后换了个国际赛事/国内没见过的品牌名，is_target_team()
    会静默认不出来、不会报错——这个函数配合调用方打印的"参赛队伍清单"日志，让人能
    肉眼从名单里发现"这支应该是目标战队但没被标记"，从而知道该往 TARGET_TEAM_ALIASES
    里补哪个别名。
    """
    teams = set()
    for raw in raw_matches:
        for key in ("hname", "gname"):
            name = raw.get(key)
            if name:
                teams.add(name)
    return sorted((name, is_target_team(name)) for name in teams)


def parse_matches(raw_matches, warn=print):
    """解析全部原始记录，只保留目标战队参赛的场次；单条记录异常只警告不中断整体。

    返回 (matches, skipped_count)：skipped_count 是因为字段缺失/时间格式非法等
    解析异常而被跳过的记录数（不包含"跟目标战队无关，正常过滤掉"的记录）。
    """
    matches = []
    skipped = 0
    for raw in raw_matches:
        try:
            match = normalize_match(raw)
        except ParseWarning as exc:
            warn(f"[WARN] {exc}")
            skipped += 1
            continue
        if not is_ag_match(match):
            continue
        if _DOMESTIC_SEASON_RE.match(match["scheduleid"]):
            for team in (match["home"], match["away"]):
                if team not in KNOWN_TEAMS and not is_target_team(team):
                    warn(
                        f"[WARN] 未知战队名：{team}（scheduleid={match['scheduleid']}），"
                        f"简称显示可能不够准确，请在 match_parser.py 补充 "
                        f"KNOWN_TEAMS / CITY_PREFIXES"
                    )
        matches.append(match)
    matches.sort(key=lambda m: m["start"])
    return matches, skipped
