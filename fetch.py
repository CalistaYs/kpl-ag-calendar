#!/usr/bin/env python3
"""从腾讯官方 KPL/TGA 开放接口拉取赛程数据。

这是 pvp.qq.com 官网页面自己在用的接口（见 pvp.qq.com/match/kpl/js/esports_index.js
里的 match_url），返回结构化 JSON：真实开赛时间、比赛地点、比分、以及官方赛程系统
自带的稳定比赛 ID（scheduleid）。不再需要解析 HTML 页面猜测赛程表格结构。

这个接口不止服务 KPL 国内联赛：用真实请求验证过，同一个 appid/sign 组合还能拉到
EWC（电竞世界杯）、KIC 等国际赛事的数据（国际赛事里 AG 的参赛队名是
"All Gamers Global"，跟国内的"成都AG超玩会"完全不一样，见 match_parser.py 的
别名匹配）。官方没有提供"赛事列表"接口（探测过 getSeasonList/getCompetitionList/
getTournamentList 等常见命名，全部返回签名校验失败，猜测这些路由本身就不存在），
所以只能用"年份 x 赛事代号模板"批量试探候选 ID，命中就收，没有就跳过。
"""
import datetime as dt
import json
import os
import urllib.parse
import urllib.request

TGA_ENDPOINT = "https://tga-openapi.tga.qq.com/openapi/tgabank/getSchedules"
TGA_APPID = "10005"
# 与官网页面 js/esports_index.js 中用于只读查询的签名一致，appid=10005 是公开只读的
# 查询凭证（该页面在浏览器端就是明文写死这个值发请求的），不涉及任何账号权限。
TGA_SIGN = "K8tjxlHDt7HHFSJTlxxZW4A+alA="

BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))

# 已经用真实请求验证过、确实能拿到数据的赛事代号模板：
#   KPL{year}S1 / S2 / S3 = 春季赛 / 夏季赛 / 年度总决赛（2024-2026 均验证过）
#   EWC{year}   = 电竞世界杯（验证过 EWC2024，AG 参赛队名是 "All Gamers Global"）
#   KIC{year}   = 国际邀请赛（验证过 KIC2025，不过该届没有 AG 参赛）
# 下面几个还没验证到真实数据（比如"挑战者杯"的官方代号规律没找到），保留作为候选：
# 猜不中只是被跳过、记一条日志，不影响其它已确认赛事；以后确认了新的代号规律，
# 或官方上线了新赛事，加进这个列表（或用 SEASON_ID_PATTERNS 环境变量）就行，
# 不需要改扫描逻辑本身。
DEFAULT_SEASON_ID_PATTERNS = [
    "KPL{year}S1",
    "KPL{year}S2",
    "KPL{year}S3",
    "KPL{year}S4",
    "EWC{year}",
    "KIC{year}",
    "KPL{year}CC",
    "KPL{year}WC",
    "HOK{year}EWC",
]

SEASON_SCAN_YEARS_ENV = "SEASON_SCAN_YEARS"
SEASON_ID_PATTERNS_ENV = "SEASON_ID_PATTERNS"


class FetchError(Exception):
    """网络异常，或 API 明确返回了非 0 的 result（签名失效、参数错误等）。"""


def _http_get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "kpl-ag-calendar/3.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    return json.loads(body)


def fetch_season_matches(season_id):
    """拉取某个赛事/赛季的全部比赛。

    不传 stage 参数即可一次性拿到该赛事所有阶段的比赛（小组赛/淘汰赛/半决赛/
    总决赛……只要官方已经排了赛程），不需要逐个猜测/枚举 stage 代码。

    season_id 不存在或该赛事还没有任何比赛时，官方接口返回空列表——这不算错误，
    只有网络异常或 API 明确报错（result != 0，例如签名失效）才会抛 FetchError。
    """
    params = urllib.parse.urlencode({
        "appid": TGA_APPID,
        "sign": TGA_SIGN,
        "seasonid": season_id,
    })
    url = f"{TGA_ENDPOINT}?{params}"
    try:
        payload = _http_get_json(url)
    except Exception as exc:
        raise FetchError(f"请求 {season_id} 失败：{exc}") from exc
    if payload.get("result") != 0:
        raise FetchError(f"API 返回错误（{season_id}）：{payload.get('msg')}")
    return payload.get("data") or []


def scan_years(now=None):
    """要扫描哪些年份，默认"今年 ± 1"；可以用 SEASON_SCAN_YEARS 环境变量覆盖
    （逗号分隔，例如 "2025,2026,2027"）。
    """
    override = os.environ.get(SEASON_SCAN_YEARS_ENV, "").strip()
    if override:
        years = [int(y) for y in override.split(",") if y.strip().isdigit()]
        if years:
            return years
    now = now or dt.datetime.now(BEIJING_TZ)
    return [now.year - 1, now.year, now.year + 1]


def season_id_patterns():
    """候选赛事代号模板列表，可以用 SEASON_ID_PATTERNS 环境变量整体覆盖（逗号分隔，
    每一项里的 {year} 会被替换成具体年份；不含 {year} 的项会被当成写死的字面量 ID，
    每个年份都会重复生成同一个值，但去重后只会真正请求一次）。
    """
    override = os.environ.get(SEASON_ID_PATTERNS_ENV, "").strip()
    if override:
        patterns = [p.strip() for p in override.split(",") if p.strip()]
        if patterns:
            return patterns
    return DEFAULT_SEASON_ID_PATTERNS


def generate_season_candidates(years=None):
    """按"年份 x 赛事代号模板"生成候选 season id 列表（去重，保持生成顺序）。"""
    years = years if years is not None else scan_years()
    seen = set()
    candidates = []
    for year in years:
        for pattern in season_id_patterns():
            season_id = pattern.format(year=year)
            if season_id not in seen:
                seen.add(season_id)
                candidates.append(season_id)
    return candidates


def scan_all_seasons(years=None, log=print):
    """扫描全部候选赛事 ID，返回 {season_id: 该赛事全部比赛(原始记录列表)}——
    只包含真正拿到数据的赛事；不存在 / 报错 / 无数据的候选记一条日志后跳过，
    不会中断整个扫描过程。
    """
    candidates = generate_season_candidates(years)
    log(f"[INFO] 本次扫描 {len(candidates)} 个候选赛事 ID：{', '.join(candidates)}")

    results = {}
    for season_id in candidates:
        try:
            matches = fetch_season_matches(season_id)
        except FetchError as exc:
            log(f"[WARN] 赛事 {season_id} 无效或拉取失败，跳过：{exc}")
            continue
        if not matches:
            log(f"[INFO] 赛事 {season_id} 目前没有比赛数据（不存在/还未开赛），跳过")
            continue
        log(f"[INFO] 赛事 {season_id} 有效，共 {len(matches)} 场比赛")
        results[season_id] = matches
    return results
