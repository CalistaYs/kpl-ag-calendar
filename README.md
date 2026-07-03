# KPL Chengdu AG Calendar

This repository publishes a subscribable calendar for Chengdu AG (成都AG超玩会) KPL matches.

## 数据来源：官方接口，不再解析网页

赛程数据来自腾讯官方的 KPL/TGA 开放接口（`getSchedules`），这也是 pvp.qq.com 官网页面自己在用的接口
（参见页面加载的 [`js/esports_index.js`](https://pvp.qq.com/match/kpl/js/esports_index.js) 里的
`match_url`）。返回的是结构化 JSON，每场比赛自带：

- 官方开赛时间（精确到分钟）与真实比赛地点（`region`，如"成都"）
- 官方比分与比赛状态（未开始/进行中/已结束）
- 一个稳定、由腾讯赛程系统分配的比赛 ID（`scheduleid`，如 `KPL2026S2M3W3D3`）

这比解析 HTML 页面/Wikipedia 表格可靠得多：不用再猜表格列顺序、不用处理页面改版、不用把整页拍平成
token 流再猜边界。之前出现过的"比分/队伍/时间互相串行、同一场比赛重复出现、UID 冲突"等问题，
根源都是在解析非结构化的 HTML；改成直接读官方结构化数据后，这一类问题不会再发生。

## 代码结构

逻辑拆成几个单一职责的模块，而不是全部堆在一个文件里：

| 文件 | 职责 |
|---|---|
| [`fetch.py`](fetch.py) | 调 TGA 官方接口拉数据；自动探测当前赛季 ID |
| [`match_parser.py`](match_parser.py) | 把官方原始字段转成日历要用的统一格式；队伍简称映射 |
| [`validator.py`](validator.py) | 生成 ICS 前的数据完整性校验 |
| [`ics_generator.py`](ics_generator.py) | 把校验通过的比赛渲染成 RFC5545 `.ics` 文本 |
| [`update_calendar.py`](update_calendar.py) | 入口脚本，只负责把上面几个模块串起来 |

GitHub Actions 仍然只需要跑 `python update_calendar.py`（[`.github/workflows/update-calendar.yml`](.github/workflows/update-calendar.yml)），
入口文件名和调用方式没变。

## 赛季自动跟随

KPL 自 2022 年起每年只有春季赛、夏季赛两个常规赛季，官方接口的赛季 ID 命名固定为
`KPL{年份}S1`（春）/ `KPL{年份}S2`（夏）——这个规律在接口里从 2021 到 2026 验证一致。

`fetch.discover_current_season()` 每次运行都会用"今年/去年的 S1、S2"这几个候选 ID 去查接口，
挑出比赛时间离"现在"最近的那个当作当前赛季，所以春/夏季赛交替、跨年份都不需要手动改代码。

如果未来出现挑战者杯、世界冠军杯等不遵循 `KPL{年}S1/S2` 命名规律的赛事，自动探测大概率找不到数据；
这种情况下可以设置 `KPL_SEASON_ID` 环境变量手动指定赛季 ID（比如在 workflow 里加一行 `env:`），
不需要改代码逻辑。只有在探测和手动指定都没设置、且探测彻底失败时，才会退回代码里写死的
`FALLBACK_SEASON_ID`（当前是 `KPL2026S2`，建议以后定期更新成最近一个已知能用的赛季 ID）。

## 数据校验与安全更新

写入 `calendar.ics` 之前，`validator.validate_matches()` 会检查：

- 每场比赛的 UID（即官方 `scheduleid`）是否唯一，出现重复直接判定失败
- 比分是否是非负整数；上限不写死成某个具体数字（不假设 KPL 只有 BO5——未来可能出现
  BO7、BO9 甚至更长的赛制）。如果官方接口带了赛制字段 `bo_total`（如 BO5=5），就按
  `ceil(bo_total/2)` 动态算出单方最多能拿到的比分（BO5 最高 3、BO7 最高 4、BO9 最高 5……）；
  没有赛制信息时默认放宽到 0–9，这样 6:5、7:6、9:8 这类比分不会被误判失败。超过这个上限
  才判定异常——这条专门用来拦截"抓到不相关数字当成比分"这类错误，历史上出现过 60:60、
  85:80 的假比分
- 开赛时间是否落在合理年份范围内
- 对阵双方队名是否合法（非空、且不是同一支队伍）
- 本次解析到的比赛数量是否比上一次 `calendar.ics` 里的数量少了一半以上（可能意味着数据源出了问题）

**任何一项检查失败，脚本都会保留现有 `calendar.ics` 不动，打印详细错误后以非零状态退出**——
这样 GitHub Actions 那次运行会显示失败（方便在 Actions 日志里定位原因），而不是把有问题的数据
悄悄提交上去覆盖旧日历。

校验通过后，脚本会先把新内容写到 `calendar.new.ics`，确认生成的事件数和解析到的比赛数一致后，
再原子性地替换成 `calendar.ics`（`os.replace`），不会出现写到一半的半成品文件。

如果当前赛季官方接口暂时没有 AG 的比赛数据（比如赛程还没公布），也不算错误，脚本会打印提示后
直接退出、不改动 `calendar.ics`，不会用旧赛季数据冒充新赛季。

脚本运行时会打印这些日志，方便在 Actions 里快速定位问题：发现比赛数量（该赛季全部战队）、
解析成功（AG 参赛场次）、未知战队名警告、重复 UID 错误、最终生成比赛数。

## 队伍名称

日历标题里，AG 一方固定显示为 `AG`；对手统一使用去掉城市前缀的队伍简称，例如：

- 重庆狼队 → 狼队
- 苏州KSG → KSG
- 济南RW侠 → RW侠
- 北京WB → WB
- 上海EDG.M → EDG.M

比赛标题格式固定为 `AG VS 对手简称`。城市前缀去除逻辑在 [`match_parser.py`](match_parser.py) 的
`CITY_PREFIXES`（城市名清单）和 `short_team_name()` 函数里维护；`KNOWN_TEAMS` 是已知战队名单，
仅用于给陌生队名打印警告（改名/扩军/缩编时），不会影响比赛是否被收录——是否是 AG 的比赛，
直接由官方接口的队名字段判断，不依赖这份名单是否完整。

## 开赛时间、地点、比分与提醒

- `DTSTART`/`DTEND` 直接用官方接口给出的真实开赛时间（`TZID=Asia/Shanghai`，北京时间 GMT+8），
  订阅后 iPhone 日历会自动换算成本地时间显示；比赛时长按官方接口没有结束时间字段，固定按 3 小时估算。
- 如果官方数据带地点，会同时写入 `LOCATION` 属性（iOS 日历会据此显示地图）和 DESCRIPTION 里的
  "比赛地点：xxx"一行；官方没给地点时两处都不会出现，不编造。
- 比赛结束（官方 `match_state` 为已结束）且带了官方比分后，DESCRIPTION 会自动加上
  "比赛结果：AG X:Y 对手简称"一行；未开赛或进行中不会出现这一行。
- 每场比赛自带两条提醒：开赛前 1 小时、开赛前 30 分钟（`VALARM`）。

## UID 与比赛延期/改期/取消/重赛

每场比赛的 `UID` 直接基于官方 `scheduleid`（如 `KPL2026S2M3W3D3`）生成，这是腾讯赛程系统给这场
比赛分配的稳定标识，不会随开赛时间、地点、比分变化。所以官方修改时间/地点、更新比分，或者比赛
延期，重新抓取后都会更新同一个日历事件（`DTSTART`/`DTEND`/`LOCATION`/`DESCRIPTION`），不会在
订阅日历里产生重复事件；如果一场比赛从官方接口的返回结果里彻底消失（取消），下次刷新后它也会从
订阅日历里消失。

## iOS 订阅方法

1. 打开本仓库的 [`calendar.ics`](calendar.ics)，点击 "Raw" 获取原始文件链接，或直接复制下面的固定链接：

```text
https://raw.githubusercontent.com/CalistaYs/kpl-ag-calendar/main/calendar.ics
```

2. 在 iPhone 上打开 **设置 > 日历 > 账户 > 添加账户 > 其他 > 添加已订阅的日历**（Settings > Calendar > Accounts > Add Account > Other > Add Subscribed Calendar）。
3. 粘贴上面的链接，保存即可。之后每次仓库更新 `calendar.ics`，订阅的日历会自动同步（iOS 有自己的刷新间隔，也可以在日历账户设置里手动设置刷新频率）。
4. 打开任意一场比赛，应能看到具体开赛时间（本地时间，并标注 GMT+8 对应的北京时间）、比赛地点（如有）以及开赛前 1 小时和 30 分钟的提醒。

## 更新方式

GitHub Actions（[`.github/workflows/update-calendar.yml`](.github/workflows/update-calendar.yml)）每 6 小时自动运行一次并提交变更。

Official KPL viewing page:
https://pvp.qq.com/match/kpl/
