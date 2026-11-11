# personal-saas-agent

个人用的 SaaS 选题 Agent。第一版只做一件事：抓 [FindNews](https://findnews.me/) 的新游戏板块，去重、跟 GPTs 比 Google Trends 热度，生成中文播报草稿，交给 Agent 补全「是做什么的」、查竞争、给出「值得做 / 可以试试 / 观望 / 不做」的判断，再发给我。

分工：代码从几百个游戏里筛出十几个并算好热度；Agent 读网页，判断哪几个真值得做、说清理由；我拍板。

Python 3.10+。查热度用 [pytrends](https://github.com/GeneralMills/pytrends)（`pip install -r requirements.txt`），不要 Key，不花钱；没装也能跑，热度会写「未测」。

## 用法

```bash
pip install -r requirements.txt
python3 findnews_games.py --slot morning
```

- 第一次运行：把 FindNews 当前列表全部记进 `state/seen.json`，只有 48 小时内首次被发现的游戏进候选
- 之后每次：只出我们没见过的新游戏；推过的游戏又被 FindNews 发现时，列进「再次被发现」
- 结果在 `out/<日期>_<slot>/`：`report.md` 是播报草稿，`games.json` 是完整数据

## 热度：GPTs × N

Google Trends 只给相对值，单查一个词永远是 100，看不出大小。所以每组 4 个游戏 + `GPTs` 放在同一张图里查（全球、近 7 天），热度 = 游戏 7 天均值 ÷ GPTs 均值。口径同 [saas-growth-agents](https://github.com/Pluviobyte/saas-growth-agents) 的新词监控：

- 均值先去掉最后 24 小时（还没结算完），最后 24 小时单独算一个倍数，只作参考
- 同组 GPTs 被大词压扁（均值 < 5）时，把小词拆出来重查，最多 2 轮
- ≥ `--min-gpts`（默认 0.05）进「新游戏」，低于的进「早期观察」
- 没测到的（被限流、没装 pytrends）照样进「新游戏」，报告开头有 ⚠，不能当作没热度

pytrends 是非官方库，靠模拟网页请求，Google 限流时每组会等 8 到 60 秒重试。十几个候选一般 1 分钟内跑完。

注意：名字太泛的（`Oh No`、`Shibuya`）热度会虚高，搜的人不是在找这个游戏，这类要靠 Agent 剔除。

## 常用参数

| 参数 | 默认 | 作用 |
|---|---|---|
| `--max-age-h` | 48 | FindNews 首次发现多久以内算新 |
| `--min-score` | 0 | 低于这个 FindNews score 的不进候选 |
| `--min-gpts` | 0.05 | 热度低于 GPTs × 这个值的进早期观察 |
| `--no-trends` | 关 | 不查 Google Trends |
| `--file` | 无 | 读本地 games.json，离线调试（不联网，也不查 Trends） |

环境变量：`AGENT_HOME`（工作目录，默认脚本所在目录）。

## 装到 Grok Bot（或别的 Agent）

把仓库推到 GitHub，新建一个 Bot，发给它这一句：

```text
读 https://github.com/<你的账号>/personal-saas-agent 的 AGENTS.md，按「安装」一节把你自己装好。一次只问我一个问题。
```

它会 clone 仓库、装依赖、跑测试、手动跑一次给你看，你确认后再存成长期指令、建例程（默认每天 09:30、17:50，Asia/Shanghai）。FindNews 每小时更新一次，跑得再勤也没有更多新东西。

## 测试

```bash
python3 tests/test_findnews.py
```

不联网，测试里临时生成 FindNews 数据和假的 Trends 图。

## 致谢

思路和 Agent 工作流参考了 [Pluviobyte/saas-growth-agents](https://github.com/Pluviobyte/saas-growth-agents)（MIT）。热度打分照它 `score_vs_gpts` 的口径用标准库重写，其余代码是独立写的。
