#!/usr/bin/env python3
"""从腾讯官方 KPL/TGA 开放接口拉取赛程数据。

这是 pvp.qq.com 官网页面自己在用的接口（见 pvp.qq.com/match/kpl/js/esports_index.js
里的 match_url），返回结构化 JSON：真实开赛时间、比赛地点、比分、以及官方赛程系统
自带的稳定比赛 ID（scheduleid）。不再需要解析 HTML 页面猜测赛程表格结构。
"""
import datetime as dt
import json
import urllib.parse
import urllib.request

TGA_ENDPOINT = "https://tga-openapi.tga.qq.com/openapi/tgabank/getSchedules"
TGA_APPID = "10005"
# 与官网页面 js/esports_index.js 中用于只读查询的签名一致，appid=10005 是公开只读的
# 查询凭证（该页面在浏览器端就是明文写死这个值发请求的），不涉及任何账号权限。
TGA_SIGN = "K8tjxlHDt7HHFSJTlxxZW4A+alA="

BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))


class FetchError(Exception):
    """网络异常，或 API 明确返回了非 0 的 result（签名失效、参数错误等）。"""


def _http_get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "kpl-ag-calendar/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    return json.loads(body)


def fetch_season_matches(season_id):
    """拉取某个赛季的全部比赛。

    不传 stage 参数即可一次性拿到该赛季所有阶段的比赛（季前赛/常规赛各轮/
    季后赛/总决赛……只要官方已经排了赛程），不需要逐个猜测/枚举 stage 代码。

    season_id 不存在或该赛季还没有任何比赛时，官方接口返回空列表——这不算错误，
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


def season_candidates(now=None):
    """按当前日期猜测可能处于进行中的赛季 ID：今年/去年的春季赛(S1)、夏季赛(S2)。

    KPL 自 2022 年起每年只有春季赛、夏季赛两个常规赛季，命名固定为
    KPL{年份}S1（春）/ KPL{年份}S2（夏）；这个规律在官方接口里从 2021 到 2026
    验证一致。如果未来出现挑战者杯/世界冠军杯等不遵循这个命名规律的赛事，
    这里猜不到，需要用 KPL_SEASON_ID 环境变量手动指定（见 update_calendar.py）。
    """
    now = now or dt.datetime.now(BEIJING_TZ)
    year = now.year
    return [
        f"KPL{year}S2",
        f"KPL{year}S1",
        f"KPL{year - 1}S2",
        f"KPL{year - 1}S1",
    ]


def discover_current_season(now=None, log=print):
    """在候选赛季 ID 里，挑出比赛时间离"现在"最近的一个，视为当前赛季。

    用"最接近当前时间"而不是"今年 S2 优先"来判断，是为了在春/夏季赛交替的
    过渡期也能选对——比如夏季赛还没开始时不会误选进行中的春季赛之外的旧数据。

    返回 (season_id, matches)；所有候选都没有可用数据（或全部请求失败）时返回
    (None, None)，由调用方决定要不要退回写死的兜底赛季 ID。
    """
    now = now or dt.datetime.now(BEIJING_TZ)
    now_ts = now.timestamp()
    best = None
    for season_id in season_candidates(now):
        try:
            matches = fetch_season_matches(season_id)
        except FetchError as exc:
            log(f"[WARN] 探测赛季 {season_id} 失败：{exc}")
            continue
        if not matches:
            continue
        closest = min(
            abs(float(m.get("match_timestamp") or 0) - now_ts) for m in matches
        )
        if best is None or closest < best[0]:
            best = (closest, season_id, matches)
    if best is None:
        return None, None
    return best[1], best[2]
