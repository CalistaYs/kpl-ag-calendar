# KPL Chengdu AG Calendar

This repository publishes a subscribable calendar for Chengdu AG (AG超玩会) KPL matches.

## 队伍名称

日历标题里，AG 一方固定显示为 `AG`；对手统一使用去掉城市前缀的队伍简称，例如：

- 重庆狼队 → 狼队
- 苏州KSG → KSG
- 济南RW侠 → RW侠
- 北京WB → WB
- 上海EDG.M → EDG.M

比赛标题格式固定为 `AG VS 对手简称`，例如 `AG VS 狼队`、`AG VS KSG`。对手队名的城市前缀去除逻辑在
[`update_calendar.py`](update_calendar.py) 的 `CITY_PREFIXES` 列表和 `short_team_name()` 函数中维护，
如果出现新战队或新城市前缀，在 `CITY_PREFIXES` 中补充即可。

## 开赛时间与提醒

- 每场比赛的 `DTSTART` / `DTEND` 使用北京时间（`TZID=Asia/Shanghai`，GMT+8），订阅后 iPhone 日历会自动换算成本地时间显示。
- 默认开赛时间为 `20:00`（北京时间），持续 3 小时；这是因为 Wikipedia 赛程页目前只公布常规赛的比赛日期、没有公布具体到分钟的开赛时间，因此该时间为估计值，DESCRIPTION 中也会注明“具体时间以官方公布为准”。如果数据源后续补充了官方具体开赛时间，请更新 `update_calendar.py` 中的时间解析逻辑，不要凭空编造。
- 每场比赛自带两条提醒：开赛前 1 小时、开赛前 30 分钟（`VALARM`）。

## iOS 订阅方法

1. 打开本仓库的 [`calendar.ics`](calendar.ics)，点击 "Raw" 获取原始文件链接，或直接复制下面的固定链接：

```text
https://raw.githubusercontent.com/CalistaYs/kpl-ag-calendar/main/calendar.ics
```

2. 在 iPhone 上打开 **设置 > 日历 > 账户 > 添加账户 > 其他 > 添加已订阅的日历**（Settings > Calendar > Accounts > Add Account > Other > Add Subscribed Calendar）。
3. 粘贴上面的链接，保存即可。之后每次仓库更新 `calendar.ics`，订阅的日历会自动同步（iOS 有自己的刷新间隔，也可以在日历账户设置里手动设置刷新频率）。
4. 打开任意一场比赛，应能看到具体开赛时间（本地时间，并标注 GMT+8 对应的北京时间）以及开赛前 1 小时和 30 分钟的提醒。

## 更新方式

`update_calendar.py` 会抓取 Wikipedia 赛程页并重新生成 `calendar.ics`；GitHub Actions
（[`.github/workflows/update-calendar.yml`](.github/workflows/update-calendar.yml)）每 6 小时自动运行一次并提交变更。

Source page:
https://zh.wikipedia.org/wiki/王者荣耀职业联赛2026年夏季赛

Official KPL viewing page:
https://pvp.qq.com/match/kpl/
