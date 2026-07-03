# AG Superplay (成都AG超玩会) Official Match Calendar

This repository publishes a subscribable calendar covering **all official esports events** the
Chengdu AG Superplay (成都AG超玩会) Honor of Kings roster appears in — not just the current KPL
season. It scans KPL's domestic splits, the year-end finals, and international events (EWC, KIC,
and whatever else the official data covers) every run, and keeps the full history around even after
a season ends.

## 数据来源：官方接口，不再解析网页

赛程数据来自腾讯官方的 KPL/TGA 开放接口（`getSchedules`），这也是 pvp.qq.com 官网页面自己在用的接口
（参见页面加载的 [`js/esports_index.js`](https://pvp.qq.com/match/kpl/js/esports_index.js) 里的
`match_url`）。返回的是结构化 JSON，每场比赛自带：

- 官方开赛时间（精确到分钟）与真实比赛地点（`region`，如"成都"）
- 官方比分、赛制（`bo_total`）与比赛状态（未开始/进行中/已结束）
- 一个稳定、由腾讯赛程系统分配的比赛 ID（`scheduleid`，如 `KPL2026S2M3W3D3`）

用真实请求验证过，这个接口不止服务 KPL 国内联赛：同一个 `appid`/`sign` 组合还能拉到 `EWC2024`
（电竞世界杯）、`KIC2025`（国际邀请赛）等国际赛事的数据——国际赛事里 AG 的参赛队名是
**"All Gamers Global"**，跟国内的"成都AG超玩会"完全不一样（见下面"目标战队识别"一节）。

这比解析 HTML 页面/Wikipedia 表格可靠得多：不用再猜表格列顺序、不用处理页面改版、不用把整页拍平成
token 流再猜边界。之前出现过的"比分/队伍/时间互相串行、同一场比赛重复出现、UID 冲突"等问题，
根源都是在解析非结构化的 HTML；改成直接读官方结构化数据后，这一类问题不会再发生。

官方没有提供"赛事列表"接口——探测过 `getSeasonList`/`getCompetitionList`/`getTournamentList` 等常见
命名，全部返回签名校验失败，判断这些路由本身就不存在（详见下一节）。

## 赛事自动发现：候选 ID 批量扫描

因为没有官方的赛事列表接口，`fetch.scan_all_seasons()` 改用"年份 x 赛事代号模板"批量试探候选
赛事 ID，命中就收、没有就跳过，不会中断整个扫描：

| 模板 | 含义 | 验证状态 |
|---|---|---|
| `KPL{year}S1` | 春季赛 | 已验证（2024-2026 均有真实数据） |
| `KPL{year}S2` | 夏季赛 | 已验证 |
| `KPL{year}S3` | 年度总决赛 | 已验证（如 `KPL2025S3` = "2025王者荣耀年度总决赛"） |
| `KPL{year}S4` | 预留给未来可能出现的第四个分段 | 未验证，命中就收 |
| `EWC{year}` | 电竞世界杯 | 已验证（`EWC2024` 里 AG 参赛队名是 "All Gamers Global"） |
| `KIC{year}` | 国际邀请赛 | 已验证（`KIC2025` 存在，但那一届没有 AG 参赛） |
| `KPL{year}CC` / `KPL{year}WC` / `HOK{year}EWC` | "挑战者杯"等赛事的猜测代号 | 未验证，暂时没找到真实数据，保留作为候选 |

默认扫描"今年 ± 1 年"（比如今年是 2026，就扫 2025/2026/2027），每个年份套用上面所有模板，去重后
逐个请求；不存在 / 报错 / 无数据的候选记一条日志后跳过，已知有效的赛事完全不受影响。

**不需要改代码就能扩展**：

- 想加一个新赛事代号模板、或者官方启用了新赛事：设置 `SEASON_ID_PATTERNS` 环境变量（逗号分隔，
  每一项里的 `{year}` 会被替换成具体年份；不含 `{year}` 的项会被当成写死的字面量 ID，比如某个
  一次性赛事的固定代号）。
- 想扩大/缩小扫描的年份范围（比如想一次性回溯更早的历史）：设置 `SEASON_SCAN_YEARS` 环境变量
  （逗号分隔，如 `2020,2021,2022,2023,2024,2025,2026,2027`）。

首次运行只会发现默认年份窗口（今年 ± 1）内的赛事；更早的历史（比如 2024 年的 EWC）需要手动把
`SEASON_SCAN_YEARS` 临时调宽做一次性回溯抓取，抓到之后会被合并进 `calendar.ics` 永久保留（见下面
"跨赛事历史保留"一节），不需要每次都用这么宽的窗口。

## 目标战队识别：支持国际赛事别名

不能只精确匹配"成都AG超玩会"这一个字符串——国际赛事里的队名完全不同（验证过的真实例子是
"All Gamers Global"）。`match_parser.is_target_team()` 用别名 + 模糊匹配识别：

- 默认别名清单：`成都AG超玩会`、`成都 AG 超玩会`、`AG超玩会`、`成都AG`、`AG`、`AG.AL`、`AG AL`、
  `AG_AL`、`All Gamers`、`All Gamers Global`。
- 匹配前先把大小写、空格、点(`.`)、下划线(`_`)、短横线(`-`)都归一化掉，所以 `AG.AL`/`AG AL`/
  `AG_AL`/`ag-al` 会被认成同一个东西。
- **中文别名、或归一化后长度 ≥ 5 的英文短语**（比如 `All Gamers`、`All Gamers Global`）：用包含匹配。
- **短英文别名**（比如 `AG`）：只用整词匹配（按分隔符切词后逐词比较），不会用 `contains("ag")`
  ——否则会把 `package`、`stage`、`magic` 这类词里恰好带着 "ag" 的普通单词也当成 AG（已经专门测试过
  不会误命中这三个词）。

可以用环境变量覆盖：

- `TARGET_TEAM`：目标战队的规范名称，默认 `成都AG超玩会`。
- `TARGET_TEAM_ALIASES`：完整别名清单（逗号分隔），设置后会整体替换默认清单（`TARGET_TEAM` 本身
  会自动补进去，不用重复写）。

## 代码结构

逻辑拆成几个单一职责的模块，而不是全部堆在一个文件里：

| 文件 | 职责 |
|---|---|
| [`fetch.py`](fetch.py) | 调 TGA 官方接口拉数据；生成候选赛事 ID 并批量扫描 |
| [`match_parser.py`](match_parser.py) | 把官方原始字段转成日历要用的统一格式；目标战队识别、队伍简称映射 |
| [`validator.py`](validator.py) | 生成 ICS 前的数据完整性校验 |
| [`ics_generator.py`](ics_generator.py) | 把校验通过的比赛渲染成 RFC5545 `.ics` 文本；跨赛事合并 |
| [`update_calendar.py`](update_calendar.py) | 入口脚本，只负责把上面几个模块串起来 |

GitHub Actions 仍然只需要跑 `python update_calendar.py`（[`.github/workflows/update-calendar.yml`](.github/workflows/update-calendar.yml)），
入口文件名和调用方式没变；国内赛事和国际赛事共用同一套 fetch/parser/validator/ics_generator，
不需要为不同赛事写不同的处理逻辑。

## 数据校验与安全更新

写入 `calendar.ics` 之前，`validator.validate_matches()` 会检查：

- 每场比赛最终会用到的 UID 是否唯一，出现重复直接判定失败（UID 一般直接用官方 `scheduleid`；如果
  不同赛事的 `scheduleid` 恰好撞车，`ics_generator.make_uid()` 会自动拼上赛事代号前缀区分开，
  校验用的是同一套逻辑算出来的最终 UID，不是裸 `scheduleid`）
- 比分是否是非负整数；上限不写死成某个具体数字（不假设只有 BO5——验证过的真实数据里，KPL 常规赛
  是 BO5、季后赛是 BO7）。如果官方接口带了赛制字段 `bo_total`（如 BO5=5），就按 `ceil(bo_total/2)`
  动态算出单方最多能拿到的比分（BO5 最高 3、BO7 最高 4、BO9 最高 5……）；没有赛制信息时默认放宽到
  0–9，这样 6:5、7:6、9:8 这类比分不会被误判失败。超过这个上限才判定异常——这条专门用来拦截
  "抓到不相关数字当成比分"这类错误，历史上出现过 60:60、85:80 的假比分
- 开赛时间是否落在合理年份范围内
- 对阵双方队名是否合法（非空、且不是同一支队伍）
- 本次解析到的比赛数量是否比上一次 `calendar.ics` 里**这次实际扫描到的这批赛事**的数量少了一半以上
  （可能意味着数据源出了问题）——这里特意只跟"这次扫描到的赛事"比，不能跟日历里累计的全部历史比赛数
  比，否则赛事一多、历史事件越攒越多，新赛季/新赛事刚开始、比赛数量还很少时就会被永远误判成异常

**任何一项检查失败，脚本都会保留现有 `calendar.ics` 不动，打印详细错误后以非零状态退出**——
这样 GitHub Actions 那次运行会显示失败（方便在 Actions 日志里定位原因），而不是把有问题的数据
悄悄提交上去覆盖旧日历。**本次没有扫描到任何目标战队的比赛**（比如新赛季赛程还没公布）也会走同样
的"保留现有文件、不覆盖"路径，只是不算错误、不会导致非零退出。

校验通过后，脚本会先把新内容（跟历史比赛合并后的完整结果，见下面"跨赛事历史保留"一节）写到
`calendar.new.ics`，确认合并后的事件数正好等于"其它赛事历史比赛数 + 本次扫描到的比赛数"后，
再原子性地替换成 `calendar.ics`（`os.replace`），不会出现写到一半的半成品文件，也不会因为合并
逻辑本身出错而丢事件或产生重复事件。

脚本运行时会打印这些日志，方便在 Actions 里快速定位问题：扫描了哪些候选赛事 ID、哪些有效/哪些
无效（不存在/请求失败）、每个有效赛事全部战队有多少场比赛、其中目标战队参赛多少场、解析异常跳过
多少场、本次扫描范围内更新/新增/移除了多少场比赛、最终合并后 `calendar.ics` 共有多少场比赛。

## 队伍名称

日历标题里，目标战队一方固定显示为 `AG`；对手统一使用去掉城市前缀的队伍简称，例如：

- 重庆狼队 → 狼队
- 苏州KSG → KSG
- 济南RW侠 → RW侠
- 北京WB → WB
- 上海EDG.M → EDG.M

国际赛事的对手（"Weibo Gaming"、"Team Falcons"……）不带这些城市前缀，原样显示。比赛标题格式固定为
`AG VS 对手简称`。城市前缀去除逻辑在 [`match_parser.py`](match_parser.py) 的 `CITY_PREFIXES`
（城市名清单）和 `short_team_name()` 函数里维护；`KNOWN_TEAMS` 是已知**国内**战队名单，只在国内
常规赛/总决赛（`scheduleid` 形如 `KPL{年}S{n}`）里检查，仅用于给陌生队名打印警告（改名/扩军/缩编
时），不会影响比赛是否被收录——国际赛事对手阵容变化很大，不维护完整名单，不在清单里不代表数据
有问题。

## 开赛时间、地点、比分与提醒

- `DTSTART`/`DTEND` 直接用官方接口给出的真实开赛时间（`TZID=Asia/Shanghai`，北京时间 GMT+8），
  订阅后 iPhone 日历会自动换算成本地时间显示；比赛时长按官方接口没有结束时间字段，固定按 3 小时估算。
- 如果官方数据带地点，会同时写入 `LOCATION` 属性（iOS 日历会据此显示地图）和 DESCRIPTION 里的
  "比赛地点：xxx"一行；官方没给地点时两处都不会出现，不编造。
- 比赛结束（官方 `match_state` 为已结束）且带了官方比分后，DESCRIPTION 会自动加上
  "比赛结果：AG X:Y 对手简称"一行；未开赛或进行中不会出现这一行。
- 每场比赛自带两条提醒：开赛前 1 小时、开赛前 30 分钟（`VALARM`）。

## UID、跨赛事历史保留与比赛延期/改期/取消/重赛

每场比赛的 `UID` 直接基于官方 `scheduleid`（如 `KPL2026S2M3W3D3`）生成，这是腾讯赛程系统给这场
比赛分配的稳定标识，不会随开赛时间、地点、比分变化，而且天然带着赛事代号（`scheduleid` 本身以
`season_id` 开头）。

`update_calendar.py` 每次运行会扫描一批候选赛事 ID（见上面"赛事自动发现"），但写回 `calendar.ics`
时会跟文件里已有的内容合并（[`ics_generator.merge_calendars()`](ics_generator.py)），而不是整份
覆盖：

- **不属于这次扫描到的赛事的历史比赛**（UID 前缀是其它赛事）——也就是这次没有请求到、或者已经
  切换过去的赛季/赛事——原样保留，不会因为赛季/赛事切换、或者某个赛事这次临时拉取失败，就从日历里
  消失；未被触及的历史事件也保留原有的 `DTSTAMP`。
- **属于这次扫描到的赛事的比赛**，以这次抓取的完整结果为准：官方修改开赛时间/地点、更新比分，
  重新抓取后会更新同一个日历事件（`DTSTART`/`DTEND`/`LOCATION`/`DESCRIPTION`），不会产生重复事件；
  如果一场比赛从官方接口的返回结果里彻底消失（比如被取消），下次刷新后它也会跟着从这个赛事里移除
  ——因为每次都是拉取"该赛事的完整赛程"，不是增量更新，所以能安全地这样处理，不会误删其它赛事的
  历史记录。

这样，春季赛结束、夏季赛开始、EWC 开始、挑战者杯开始……历史赛程都会一直保留在 `calendar.ics` 里。

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
