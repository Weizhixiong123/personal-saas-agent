# personal-saas-agent

个人用的游戏新词选题 Agent：盯 Steam 即将发售和新品、Roblox 飙升新游，抢在 Google 有人大量搜索之前，找到值得做攻略站的游戏词。

分工：

- **代码**（`game_radar.py`）：从几百个 Steam 游戏里筛出十几个，查好在线人数、评测走势、讨论区热帖、Google 联想词、.wiki 域名占用情况，生成中文草稿。
- **Agent**（比如 Grok Bot，按 [AGENTS.md](AGENTS.md)）：去评测区、讨论区、X、Reddit 读舆情，查竞品，按 6 项 30 分打分，给出「做 / 观察一天 / 放弃」。
- **我**：拍板，注册域名、建站。

只用 Python 3.10+ 标准库，不用装依赖，不要 Key，不花钱。

## 用法

```bash
python3 game_radar.py --slot morning
python3 game_radar.py --slot morning --no-save   # 试跑，不写账本
```

结果在 `out/<日期>_<slot>/`：`report.md` 是播报草稿，`games.json` 是完整数据（含评测原文样本）。同一时段重跑会另建 `_2` 目录，不覆盖。

## 数据从哪来

| 来源 | 用途 |
|---|---|
| Steam 热门即将发售（`popularcomingsoon`，前 150） | 主来源，按热度排好序，主要看愿望单 |
| Steam 热门新品（`popularnew`，前 100） | 刚发售的，只收 14 天以内 |
| Steam 按发售日（`comingsoon`，前 100） | 补漏，试玩版在线 ≥ 250 才收 |
| Steam 在线人数接口 | 正式版和试玩版的在线人数，发售前最准的热度信号 |
| Steam 评测走势、评测原文、讨论区 | 评测日增量、好评率、英文占比、玩家在问什么 |
| Google 联想词 | 输入「游戏名 」后有没有 guide / wiki / codes，判断真有没有人搜 |
| RDAP（被限额时改用 DNS） | 7 种 .wiki 域名是否已注册 |
| FindNews 新游戏 | 补充线索，不在 Steam 榜单上的新词 |
| Roblox `up-and-coming`、`top-trending` 榜单 | 在线人数、好评率、创建日期，找飙升的 Roblox 游戏 |

SteamDB 会拦截自动抓取，草稿里每个游戏附了 SteamDB 链接，手动看。

## 收录规则

- 已发售 14 天以内、或 14 天内发售：进「新游戏」
- 15 到 49 天内发售、或发售日未定：试玩版在线 ≥ 250 才进「新游戏」（标「可提前进场」），否则进「早期观察」，每轮再看
- 推过的游戏，发售后第 3 到 7 天进「复查」一次
- 一轮最多展开 15 个，按在线人数排，多出来的下轮再看
- Roblox 看的是在线人数暴涨的时间点，不是创建日期（竞品多在创建 3 到 7 周后爆火时才进场）：
  - 创建 14 天以内的新游：在线 ≥ 1,000，或比 24 小时前涨 20% 以上
  - 创建 15 到 90 天的老游戏：比 24 小时前涨 50% 以上才算暴涨；第一次见到只记基线，第二天才能判断
  - 都要求在线 500 到 15,000 人、好评率 ≥ 85%
  - 涨幅一律跟约 24 小时前（同一时段）比，不跟上一轮比：早报和晚报隔着美国的高峰和低谷，跟上一轮比会得到假涨跌
- 账本 `state/seen.json` 在报告写完之后才保存，跑到一半被打断，下次还会再出

## 常用参数

| 参数 | 默认 | 作用 |
|---|---|---|
| `--pre-days` | 14 | 几天内发售的直接进新游戏 |
| `--early-days` | 49 | 几天内发售的看试玩版热度 |
| `--max-released-days` | 14 | 已发售超过几天算老词 |
| `--min-demo` | 250 | 试玩版在线人数门槛 |
| `--max-new` | 15 | 一轮最多展开几个新游戏 |
| `--budget` | 600 | 一轮最多跑几秒 |
| `--no-findnews` | 关 | 不抓 FindNews |
| `--no-roblox` | 关 | 不抓 Roblox |
| `--roblox-new-days` | 14 | Roblox 创建几天内算新游 |
| `--roblox-max-days` | 90 | Roblox 创建超过几天不看 |
| `--roblox-spike` | 50 | 老游戏比 24 小时前涨多少百分比算暴涨 |
| `--roblox-min-ccu` / `--roblox-max-ccu` | 500 / 15000 | Roblox 在线人数范围 |
| `--roblox-min-like` | 85 | Roblox 最低好评率（%） |
| `--no-save` | 关 | 试跑，不写账本 |

环境变量：`AGENT_HOME`（工作目录，默认脚本所在目录）。

## 已知限制

- Steam 评测原文接口在部分网络下会被重定向到首页，这时只有评测数量和好评率，报告开头有 ⚠，Agent 要自己去商店页读评测。
- RDAP 有查询限额，被拒后改用 DNS 判断，没有注册日期；注册了但没解析的域名会被算成空闲，注册前要再确认。

## 装到 Grok Bot（或别的 Agent）

把仓库推到 GitHub，新建一个 Bot，发给它这一句：

```text
读 https://github.com/<你的账号>/personal-saas-agent 的 AGENTS.md，按「安装」一节把你自己装好。一次只问我一个问题。
```

## 测试

```bash
python3 tests/test_radar.py
```

不联网，测试里用假的 Steam、Roblox、Google、RDAP、FindNews 接口。

## 致谢

思路参考了 [Pluviobyte/saas-growth-agents](https://github.com/Pluviobyte/saas-growth-agents)（MIT），代码是独立写的。
