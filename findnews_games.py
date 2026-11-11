#!/usr/bin/env python3
"""FindNews 新游戏雷达：抓 findnews.me 的新游戏板块，去重、跟 GPTs 比 Google Trends 热度，出中文播报草稿。
用法: python3 findnews_games.py [--slot morning] [--max-age-h 48] [--min-score 0] [--min-gpts 0.05] [--no-trends] [--file games.json]

数据：https://findnews.me/data/games.json（公开 JSON，每小时更新，不要 Key，不花钱）
  每个游戏：name、series（7 天搜索热度快照，相对值）、first/last（FindNews 首次/最近发现）、score、sightings
只做「新游戏」板块，不抓 FindNews 的全球热搜。

候选 = 我们没见过 + FindNews 首次发现在 --max-age-h 小时内 + score ≥ --min-score。
更早的只记进 state/seen.json、不出候选，所以第一次运行不会把整个列表刷出来。

热度（口径同 saas-growth-agents 的 newterm）：pytrends 查 Google Trends 全球近 7 天，每组 4 个游戏 + GPTs 同图，
  GPTs × N = 游戏 7 天均值 ÷ GPTs 均值，都先去掉最后 24h（没结算完）。≥ --min-gpts 进「新游戏」，低于的进「早期观察」。
  没测到（没装 pytrends、被限流、--no-trends、--file 离线调试）的照样进「新游戏」，热度写「未测」。

工作目录取 AGENT_HOME，默认脚本所在目录：
  state/seen.json                  见过的游戏（去重账本）
  out/<YYYYMMDD>_<slot>/games.json 本轮结果
  out/<YYYYMMDD>_<slot>/report.md  中文播报草稿（「是做什么的」「竞争」「判断」留给 Agent 查完再填）
退出码：0 拿到了 FindNews 数据（Trends 没测全时报告开头有 ⚠）；1 拿不到数据（不能当作「今天没有新游戏」）。"""
import os, re, sys, json, time, random, argparse, datetime as dt
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

URL = os.environ.get('FINDNEWS_GAMES_URL', 'https://findnews.me/data/games.json')
HOME = os.environ.get('AGENT_HOME') or os.path.dirname(os.path.abspath(__file__))
SEENF = os.path.join(HOME, 'state', 'seen.json')
TZ = dt.timezone(dt.timedelta(hours=8))   # 播报统一用北京时间
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36'
STALE_H = 6   # FindNews 超过这么久没更新就在报告里提醒
GPTS = 'GPTs'
GPTS_SQUASHED = 5   # 同组 GPTs 均值低于这个 = 被组里的大词压扁，小词拆出来重查
TRENDS_MAX = 40     # 一轮最多查这么多个游戏，防止被限流卡太久

def ukey(s):
    """去重键：小写、去标点空格，保留中日韩文字。"""
    return re.sub(r'[\W_]+', '', (s or '').lower())

def parse_t(s):
    try: return dt.datetime.fromisoformat((s or '').replace('Z', '+00:00'))
    except ValueError: return None

def bj(s, fmt='%Y-%m-%d %H:%M'):
    t = parse_t(s)
    return t.astimezone(TZ).strftime(fmt) if t else ''

def mean(xs):
    return sum(xs) / len(xs) if xs else 0

def trend_of(series):
    """FindNews 发现时那条 7 天曲线的走势：上升 / 回落 / 平稳 / 无数据。"""
    xs = [x for x in (series or []) if isinstance(x, (int, float))]
    if not xs or max(xs) <= 0: return '无数据'
    peak, tail, head = max(xs), xs[-6:], xs[:-6] or [0]
    if mean(tail) >= max(mean(head) * 1.5, 10): return '上升'
    if xs[-1] < peak * 0.3: return '回落'
    return '平稳'

def vs_gpts(times, cols, terms):
    """一张 Trends 图（times + 每个词的值，含 GPTs）→ ({词: 打分}, GPTs 均值)。口径同 newterm 的 score_vs_gpts。"""
    if not times: return {t: dict(ratio=0, last24=0, mean=0, trend='无数据') for t in terms}, 0
    cut = max(times) - dt.timedelta(hours=24)
    old = [i for i, t in enumerate(times) if t <= cut]
    new = [i for i, t in enumerate(times) if t > cut]
    g = mean([cols[GPTS][i] for i in old])
    out = {}
    for t in terms:
        v = cols.get(t) or [0] * len(times)
        m = mean([v[i] for i in old])
        c = dict(ratio=round(m / g, 2) if g > 0 else 0, last24=round(mean([v[i] for i in new]) / g, 2) if g > 0 else 0, mean=m)
        if g == 0 and m > 0: c['err'] = 'GPTs 被压成 0，算不出倍数（多为新闻爆点）'
        days = {}
        for i in old: days.setdefault(times[i].date(), []).append(v[i])
        daily = [mean(days[d]) for d in sorted(days)]
        peak, last = max(daily, default=0), (daily[-1] if daily else 0)
        first = mean(daily[:2]) if len(daily) >= 2 else 0
        if peak == 0: c['trend'] = '无数据'
        elif last >= peak * 0.9 and last > first * 1.2: c['trend'] = '在涨'
        elif last >= peak * 0.7: c['trend'] = '到顶/高位'
        else: c['trend'] = '在回落'
        out[t] = c
    return out, g

def pytrends_fetch(kws):
    """pytrends 取全球近 7 天 interest_over_time → (UTC 时间列表, {词: 值列表})，丢掉没结算完的点。"""
    from pytrends.request import TrendReq   # 只有测 Trends 时才需要
    pt = TrendReq(hl='en-US', tz=0, timeout=(10, 30))
    pt.build_payload(kws, timeframe='now 7-d', geo='')
    df = pt.interest_over_time()
    if df.empty: return [], {k: [] for k in kws}   # 全是 0 时 pytrends 返回空表
    if 'isPartial' in df: df = df[~df['isPartial'].astype(bool)]
    times = [t.to_pydatetime().replace(tzinfo=dt.timezone.utc) for t in df.index]
    return times, {k: [float(x) for x in df[k]] for k in kws}

def score_trends(cands, log, fetch=pytrends_fetch, sleep=time.sleep, tries=3):
    """每组 4 个游戏 + GPTs 查 Trends，结果写进每个候选的 gpts 字段；返回请求次数。
    GPTs 被压扁的组，把均值不到组内最大词 30% 的小词拆出来重查（最多 2 轮）。"""
    calls, gap = [0], [8.0]   # gap 自适应：429 后加大，成功后慢慢回落
    def get(kws):
        for i in range(tries):
            if calls[0]: sleep(gap[0] + random.uniform(0, 4))
            calls[0] += 1
            try:
                r = fetch(kws); gap[0] = max(8.0, gap[0] * 0.85); return r
            except Exception as e:
                if '429' in str(e): gap[0] = min(60.0, gap[0] * 1.6)
                log.append(f'trends {kws[:-1]} 第 {i + 1} 次失败: {type(e).__name__}: {str(e)[:80]}')
                sleep(20 * (i + 1))
        return None
    for c in cands[TRENDS_MAX:]: c['gpts'] = dict(err=f'超过一轮 {TRENDS_MAX} 个的上限，没查')
    batch, rnd = cands[:TRENDS_MAX], 0
    while batch:
        retry = []
        for i in range(0, len(batch), 4):
            grp = batch[i:i + 4]
            got = get([c['name'] for c in grp] + [GPTS])
            if got is None:
                for c in grp: c['gpts'] = dict(err='Trends 没查到')
                continue
            sc, g = vs_gpts(*got, [c['name'] for c in grp])
            top = max(x['mean'] for x in sc.values())
            small = [c for c in grp if sc[c['name']]['mean'] < top * 0.3]
            if g < GPTS_SQUASHED and rnd < 2 and small:
                log.append(f'GPTs 被压扁（均值 {g:.1f}），重查：{[c["name"] for c in small]}')
                retry += small
            for c in grp:
                if c not in retry: c['gpts'] = sc[c['name']]
        batch, rnd = retry, rnd + 1
    return calls[0]

def load_json(p, default):
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except (OSError, ValueError): return default

def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)

def fetch(log, timeout):
    try:
        with urlopen(Request(URL, headers={'User-Agent': UA}), timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except HTTPError as e:
        log.append(f'games.json: http {e.code}')
    except Exception as e:
        log.append(f'games.json: 失败 {type(e).__name__}: {e}')
    return None

def collect(data, seen, now, max_age_h, min_score):
    """对照 seen 账本，返回 (首次候选, 再次被发现, 列表里的游戏数)；会原地更新 seen。"""
    stamp = now.strftime('%Y-%m-%d %H:%M')
    new, back, n = [], [], 0
    for g in data.get('items') or []:
        name = (g.get('name') or '').strip()
        ck = ukey(name)
        if not ck: continue
        n += 1
        series = g.get('series') or [0]
        info = dict(name=name, score=g.get('score') or 0, sightings=g.get('sightings') or 0,
                    fn_first=bj(g.get('first')), fn_last=bj(g.get('last')), trend=trend_of(series),
                    trends_url='https://trends.google.com/trends/explore?date=now%207-d&q=' + quote(name) + ',' + GPTS)
        first = parse_t(g.get('first'))
        fresh = bool(first and (now - first).total_seconds() <= max_age_h * 3600)
        s = seen.get(ck)
        if s is None:
            s = seen[ck] = dict(info, first_seen=stamp, candidate=fresh and info['score'] >= min_score)
            if s['candidate']: new.append(ck)
        elif s.get('candidate') and info['sightings'] > (s.get('sightings') or 0):
            back.append(ck)   # 推过的游戏又被 FindNews 发现一次，热度可能还在涨
        s.update(info); s['last_seen'] = stamp
    pick = lambda ks: sorted((dict(seen[k]) for k in ks), key=lambda x: -x['score'])
    return pick(new), pick(back), n

def split(cands, min_gpts):
    """测到热度且低于门槛的进早期观察；其余进新游戏，测到的按 GPTs 倍数排在前，没测到的按 score 排在后。"""
    measured = lambda x: 'ratio' in x.get('gpts', {}) and 'err' not in x['gpts']
    watch = [x for x in cands if measured(x) and x['gpts']['ratio'] < min_gpts]
    keep = [x for x in cands if x not in watch]
    keep.sort(key=lambda x: (not measured(x), -(x['gpts']['ratio'] if measured(x) else 0), -x['score']))
    watch.sort(key=lambda x: -x['gpts']['ratio'])
    return keep, watch

def heat(x):
    g = x.get('gpts') or {}
    if 'ratio' not in g or 'err' in g: return f"未测（{g.get('err') or '本轮没查 Trends'}）", None
    return f"GPTs × {g['ratio']:g}（近 24h × {g['last24']:g}）", g['trend']

def render(res):
    """手机友好的中文播报草稿：不用表格，每个游戏一段。"""
    L = [f"**FindNews 新游戏 · {res['at']}**",
         f"新游戏 {len(res['candidates'])} · 早期观察 {len(res['watch'])} · 再次被发现 {len(res['recurring'])}"]
    if not res['feed_ok']:
        L += ['', '⚠ 这轮没拿到 FindNews 数据，不代表没有新游戏：' + '；'.join(res['log'])]
        return '\n'.join(L) + '\n'
    if res['stale']: L += ['', f"⚠ FindNews 数据停在 {res['updated']}，超过 {STALE_H} 小时没更新"]
    t = res['trends']
    if t['missed']: L += ['', f"⚠ Google Trends 有 {t['missed']} 个游戏没测到（{t['note']}），它们的热度未知，不代表没热度"]
    L += ['', '一、新游戏']
    if not res['candidates']: L.append('无')
    for i, x in enumerate(res['candidates'], 1):
        h, tr = heat(x)
        L += ['', f"**{i}. {x['name']}**", '',
              f"- 热度：{h}",
              f"- 走势：{tr}" if tr else f"- 走势：FindNews 发现时{x['trend']}",
              f"- 来源：FindNews 新游戏 · 首次发现 {x['fn_first'][5:]} · score {x['score']}，被发现 {x['sightings']} 次",
              '- 是做什么的：待查',
              '- 竞争：待查',
              '- 判断：待评',
              f"- 趋势：{x['trends_url']}"]
    L += ['', f"二、早期观察（低于 GPTs × {res['min_gpts']:g}）"]
    if not res['watch']: L.append('无')
    for x in res['watch']:
        L.append(f"- {x['name']}：GPTs × {x['gpts']['ratio']:g}，{x['gpts']['trend']}，首次发现 {x['fn_first'][5:]}")
    L += ['', '三、再次被发现']
    if not res['recurring']: L.append('无')
    for x in res['recurring']:
        L.append(f"- {x['name']}：被发现 {x['sightings']} 次，发现时{x['trend']}，最近 {x['fn_last'][5:]}")
    return '\n'.join(L) + '\n'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slot', default='', help='时段标签，例如 morning / evening；默认用当前时分')
    ap.add_argument('--max-age-h', type=float, default=float(os.environ.get('FINDNEWS_MAX_AGE_H', 48)))
    ap.add_argument('--min-score', type=float, default=float(os.environ.get('FINDNEWS_MIN_SCORE', 0)))
    ap.add_argument('--min-gpts', type=float, default=float(os.environ.get('FINDNEWS_MIN_GPTS', 0.05)),
                    help='热度低于 GPTs × 这个值的进早期观察')
    ap.add_argument('--no-trends', action='store_true', help='不查 Google Trends')
    ap.add_argument('--timeout', type=int, default=60)
    ap.add_argument('--file', help='读本地 games.json（离线调试用），不联网，也不查 Trends')
    a = ap.parse_args()
    now = dt.datetime.now(TZ); log = []
    out = os.path.join(HOME, 'out', now.strftime('%Y%m%d') + '_' + (a.slot or now.strftime('%H%M')))

    data = load_json(a.file, None) if a.file else fetch(log, a.timeout)
    feed_ok = isinstance(data, dict) and isinstance(data.get('items'), list)
    if data is not None and not feed_ok: log.append('games.json: 格式不对，没有 items 列表')
    updated = parse_t((data or {}).get('updated')) if feed_ok else None
    stale = bool(updated and (now - updated).total_seconds() > STALE_H * 3600)

    seen = load_json(SEENF, {})
    cands, back, n = collect(data, seen, now, a.max_age_h, a.min_score) if feed_ok else ([], [], 0)
    if feed_ok: save_json(SEENF, seen)   # 拿不到数据时不动账本

    trends = dict(calls=0, missed=0, note='')
    if cands and (a.no_trends or a.file):
        trends['note'] = '--no-trends' if a.no_trends else '离线调试'
        for c in cands: c['gpts'] = dict(err=f"{trends['note']}，没查 Trends")
    elif cands:
        try:
            import pytrends   # noqa: F401
            trends['calls'] = score_trends(cands, log)
        except ImportError:
            log.append('trends: 没装 pytrends（pip install -r requirements.txt）')
            for c in cands: c['gpts'] = dict(err='没装 pytrends')
        trends['missed'] = sum(1 for c in cands if 'err' in c['gpts'])
        trends['note'] = '；'.join(sorted({c['gpts']['err'] for c in cands if 'err' in c['gpts']}))
    keep, watch = split(cands, a.min_gpts)

    res = dict(at=now.strftime('%Y-%m-%d %H:%M'), feed_ok=feed_ok, stale=stale,
               updated=bj((data or {}).get('updated')), games=n, source=a.file or URL,
               max_age_h=a.max_age_h, min_score=a.min_score, min_gpts=a.min_gpts, trends=trends, log=log,
               candidates=keep, watch=watch, recurring=back[:30])
    save_json(os.path.join(out, 'games.json'), res)
    with open(os.path.join(out, 'report.md'), 'w', encoding='utf-8') as f: f.write(render(res))
    print(f"findnews: feed_ok={feed_ok}，{n} 个游戏，新游戏 {len(keep)}，早期观察 {len(watch)}，再次被发现 {len(back)}，"
          f"Trends 请求 {trends['calls']} 次、没测到 {trends['missed']} 个")
    print('结果：' + out)
    for l in log: print(' ', l)
    sys.exit(0 if feed_ok else 1)

if __name__ == '__main__':
    main()
