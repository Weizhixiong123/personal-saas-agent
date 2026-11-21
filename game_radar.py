#!/usr/bin/env python3
"""游戏新词雷达：盯 Steam 即将发售和新品，抢在 Google 有人搜之前找到值得做攻略站的游戏词。
用法: python3 game_radar.py --slot <morning|evening> [--no-save] [--max-new 15] [--min-demo 250] [--no-roblox]

不用任何对比词（GPTs、锚点都不用），热度只看游戏自己的数据：
  来源      Steam 热门即将发售（主）、热门新品、按发售日排序（补漏，要试玩版够热才收）、FindNews 新游戏（补充）
  热度      试玩版 / 正式版在线人数、榜单排名、评测数、讨论区帖子
  趋势      评测每日新增（评测走势接口）
  有人搜吗  Google 搜索框联想词里有没有 guide / wiki / codes 这类词
  竞争      7 种 .wiki 域名是否已注册（RDAP，被限额时改用 DNS）
  舆情      评测统计和样本、讨论区热帖标题；X 上的讨论由 Grok 自己搜

收录窗口：
  已发售 ≤ --max-released-days（14）天、或 --pre-days（14）天内发售  → 新游戏（「按发售日」来源的要试玩版 ≥ --min-demo）
  15 到 --early-days（49）天内发售、或发售日未定                     → 试玩版 ≥ --min-demo 才进新游戏（可提前进场），否则进早期观察，下轮再看
  推过的游戏发售后第 3 到 7 天                                       → 复查一次（加码还是停手）

工作目录取 AGENT_HOME，默认脚本所在目录：
  state/seen.json                  账本：推过、观察中、排除的游戏（报告写完才保存）
  out/<YYYYMMDD>_<slot>/games.json 本轮完整数据（含评测原文样本）
  out/<YYYYMMDD>_<slot>/report.md  中文播报草稿；目录已存在时加 _2、_3，不覆盖
退出码：0 至少拿到一个 Steam 榜单；1 Steam 榜单全部拿不到（不能当作「今天没有新游戏」，账本不动）。"""
import os, re, sys, json, time, html, uuid, argparse, statistics, unicodedata, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

HOME = os.environ.get('AGENT_HOME') or os.path.dirname(os.path.abspath(__file__))
STATEF = os.path.join(HOME, 'state', 'seen.json')
TZ = dt.timezone(dt.timedelta(hours=8))   # 播报统一用北京时间
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36'

SEARCH = 'https://store.steampowered.com/search/results/?filter={f}&infinite=1&start={s}&count=50&cc=us&l=english'
SOURCES = (('popularcomingsoon', 3, '热门即将发售'), ('popularnew', 2, '热门新品'), ('comingsoon', 2, '按发售日'))
FILLER = '按发售日'   # 只出现在这个榜单里的，要试玩版够热才收
DETAILS = 'https://store.steampowered.com/api/appdetails?appids={}&cc=us&l=english'
PLAYERS = 'https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={}'
HISTO = 'https://store.steampowered.com/appreviewhistogram/{}?l=english&review_score_preference=0'
REVIEWS = 'https://store.steampowered.com/appreviews/{}?json=1&cursor=*&filter={}&language=all&num_per_page=100&purchase_type=all'
FORUM = 'https://steamcommunity.com/app/{}/discussions/0/'
SUGGEST = 'https://suggestqueries.google.com/complete/search?client=firefox&hl=en&q={}'
RDAP = 'https://rdap.nic.wiki/domain/{}'
DOH = 'https://dns.google/resolve?name={}&type=NS'
FINDNEWS = os.environ.get('FINDNEWS_GAMES_URL', 'https://findnews.me/data/games.json')
STORE = 'https://store.steampowered.com/app/{}/'
STEAMDB = 'https://steamdb.info/app/{}/'
RBX_SORTS = 'https://apis.roblox.com/explore-api/v1/get-sorts?device=computer&country=all&sessionId={}'
RBX_CONTENT = 'https://apis.roblox.com/explore-api/v1/get-sort-content?sessionId={}&sortId={}'
RBX_GAMES = 'https://games.roblox.com/v1/games?universeIds={}'
RBX_VOTES = 'https://games.roblox.com/v1/games/votes?universeIds={}'
RBX_URL = 'https://www.roblox.com/games/{}/'
RBX_CHARTS = ('up-and-coming', 'top-trending')
RBX_WIKI_KW = ('simulator', 'defense', 'anime', 'rng', 'tycoon', 'rpg', 'codes', 'discord.gg')

DETAILS_TTL_H = 72     # 商店详情缓存多久
GUIDE_WORDS = ('guide', 'guides', 'wiki', 'codes', 'code', 'map', 'recipe', 'recipes', 'build', 'builds', 'tier list',
               'walkthrough', 'achievements', 'achievement', 'tips', 'crafting', 'boss', 'bosses', 'items', 'weapons',
               'characters', 'cheats', 'system requirements', 'requirements', 'classes', 'cards', 'units', 'quests',
               'endings', 'puzzle', 'solution', 'seeds', 'mods')
REVIEW_GUIDE = ('guide', 'wiki', 'how to', 'stuck', 'confusing', 'boss', 'craft', 'recipe', 'tutorial', 'lost', 'figure out')
REVIEW_PERF = ('crash', 'crashes', 'fps', 'stutter', 'lag', 'optimization', 'optimized', 'performance', 'freeze')
BIG = ('ubisoft', 'electronic arts', 'activision', 'blizzard', 'bandai namco', 'square enix', 'sony interactive',
       'playstation', 'xbox game studios', 'microsoft', 'sega', 'capcom', '2k', 'take-two', 'rockstar', 'bethesda',
       'warner bros', 'wb games', 'nintendo', 'konami', 'tencent', 'netease', 'krafton', 'nexon', 'plaion',
       'deep silver', 'thq nordic', 'paradox interactive', 'koei tecmo', 'cd projekt', 'riot games', 'epic games',
       'valve', 'hoyoverse', 'mihoyo', 'ncsoft', 'pearl abyss', 'level-5', 'focus entertainment', 'nacon')


# ---------- 网络 ----------

def urlopen_fetch(url, timeout):
    """真实请求 → (状态码, 正文)；HTTP 错误也返回状态码，网络错误抛异常。"""
    try:
        with urlopen(Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en-US,en'}), timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except HTTPError as e:
        return e.code, (e.read() or b'').decode('utf-8', 'replace')[:500]


class Net:
    """所有请求都走这里：429/5xx 退避重试，一轮有总时间上限，错误记进 log。测试时换掉 fetch。"""
    def __init__(self, log, fetch=urlopen_fetch, sleep=time.sleep, timeout=20, budget=600):
        self.log, self.fetch, self.sleep, self.timeout = log, fetch, sleep, timeout
        self.deadline = time.monotonic() + budget
        self.over = False

    def get(self, url, tries=3):
        err = ''
        for i in range(tries):
            if time.monotonic() > self.deadline:
                self.over = True
                return None, '超过本轮时间上限，没请求'
            try:
                st, body = self.fetch(url, self.timeout)
            except Exception as e:
                st, body, err = None, '', f'{type(e).__name__}: {str(e)[:80]}'
            if st not in (None, 429, 500, 502, 503, 504) or i == tries - 1:
                return st, body if st else err
            self.sleep(3 * (i + 1))
        return None, err

    def json(self, url, what):
        st, body = self.get(url)
        if st == 200:
            try: return json.loads(body)
            except ValueError: err = '返回的不是 JSON（多半被重定向或拦截）'
        else:
            err = f'http {st}' if st else body
        self.log.append(f'{what}: {err}')
        return None

    def text(self, url, what):
        st, body = self.get(url)
        if st == 200: return body
        self.log.append(f'{what}: ' + (f'http {st}' if st else body))
        return None


def pmap(fn, items, workers):
    items = list(items)
    if not items: return []
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))


# ---------- 解析 ----------

def parse_date(s):
    s = re.sub(r'\s+', ' ', (s or '').replace('.', '')).strip()
    for f in ('%b %d, %Y', '%d %b, %Y', '%B %d, %Y', '%d %B, %Y', '%b %d %Y', '%Y-%m-%d'):
        try: return dt.datetime.strptime(s, f).date()
        except ValueError: pass
    return None


def parse_search(h):
    """Steam 搜索结果 HTML → [{appid, name, release_raw}]，跳过礼包和捆绑包。"""
    out = []
    for appid, body in re.findall(r'<a [^>]*data-ds-appid="(\d+)"[^>]*>(.*?)</a>', h or '', re.S):
        name = re.search(r'<span class="title">(.*?)</span>', body, re.S)
        rel = re.search(r'search_released[^>]*>(.*?)</div>', body, re.S)
        clean = lambda m: re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', m.group(1)))).strip() if m else ''
        if clean(name): out.append(dict(appid=appid, name=clean(name), release_raw=clean(rel)))
    return out


def short_title(name):
    """「Foo: Bar Edition」→「Foo」，竞品域名一般只用主标题。"""
    return re.split(r'\s*[:：|–—]\s*|\s+-\s+', name)[0].strip() or name


def ukey(s):
    return re.sub(r'[\W_]+', '', (s or '').lower())


def wiki_domains(name):
    """全名和主标题各 7 种命名（「Casualties: Unknown」两种都有人注册），去重。"""
    out = []
    for t in dict.fromkeys([name, short_title(name)]):
        t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode().lower()
        words = re.findall(r'[a-z0-9]+', re.sub(r"['’]", '', t))
        if not words: continue
        s, h = ''.join(words), '-'.join(words)
        out += [f'{s}.wiki', f'{s}wiki.wiki', f'{s}guide.wiki', f'{s}guides.wiki', f'{s}hub.wiki', f'{h}.wiki', f'{s}db.wiki']
    return list(dict.fromkeys(out))


def search_name(name):
    """联想词用的名字：主标题只剩一个词（太泛）时用全名。"""
    t = short_title(name)
    return t if len(t.split()) > 1 or t == name else name


def has_word(text, words):
    return any(re.search(r'\b' + re.escape(w) + r'\b', text) for w in words)


# ---------- 采集 ----------

def details(net, appid):
    j = net.json(DETAILS.format(appid), f'商店详情 {appid}')
    d = (j or {}).get(str(appid)) or {}
    if not d.get('success'):
        if j is not None: net.log.append(f'商店详情 {appid}: success=false（下架或锁区）')
        return None
    d, rd = d.get('data') or {}, (d.get('data') or {}).get('release_date') or {}
    rel = parse_date(rd.get('date'))
    price = (d.get('price_overview') or {}).get('final_formatted') or ('免费' if d.get('is_free') else '')
    descriptor_ids = set((d.get('content_descriptors') or {}).get('ids') or [])
    return dict(type=d.get('type') or '', name=d.get('name') or '', release=rel.isoformat() if rel else None,
                release_raw=rd.get('date') or '', demos=[str(x['appid']) for x in d.get('demos') or [] if x.get('appid')],
                developers=d.get('developers') or [], publishers=d.get('publishers') or [], price=price,
                genres=[g.get('description') for g in d.get('genres') or []],
                english='English' in (d.get('supported_languages') or ''),
                reviews_total=(d.get('recommendations') or {}).get('total'), nsfw=bool({3, 4} & descriptor_ids))


def players(net, appid):
    """当前在线人数；没发售（接口 result=42）或查不到返回 None。"""
    st, body = net.get(PLAYERS.format(appid))
    if st == 200:
        try:
            r = json.loads(body).get('response') or {}
            return r.get('player_count') if r.get('result') == 1 else None
        except ValueError: pass
    if st not in (200, 404): net.log.append(f'在线人数 {appid}: ' + (f'http {st}' if st else body))
    return None


def review_hist(net, appid, today):
    """评测走势接口：总评测数、总好评率、近 3 天和之前 4 天的日均新增、近 7 天好评率。"""
    j = net.json(HISTO.format(appid), f'评测走势 {appid}')
    r = (j or {}).get('results')
    if not isinstance(r, dict): return None
    day = lambda x: dt.datetime.fromtimestamp(x['date'], dt.timezone.utc).date()
    rec = sorted((day(x), x.get('recommendations_up', 0), x.get('recommendations_down', 0)) for x in r.get('recent') or [])
    rec = [x for x in rec if x[0] < today]   # 今天还没过完
    up = sum(x.get('recommendations_up', 0) for x in r.get('rollups') or [])
    down = sum(x.get('recommendations_down', 0) for x in r.get('rollups') or [])
    daily = lambda xs: round(sum(u + d for _, u, d in xs) / len(xs), 1) if xs else None
    pos = lambda u, n: round(100 * u / n) if n else None
    r7 = rec[-7:]
    return dict(total=up + down, positive=pos(up, up + down), last3_daily=daily(rec[-3:]), prev4_daily=daily(rec[-7:-3]),
                recent7_positive=pos(sum(u for _, u, _ in r7), sum(u + d for _, u, d in r7)))


def review_texts(net, appid):
    """评测原文（近期 100 条 + 最有帮助 100 条）→ 统计和样本。接口被拦截时返回 None。"""
    rs = {}
    for f in ('recent', 'all'):
        j = net.json(REVIEWS.format(appid, f), f'评测原文 {appid}')
        if not j or not j.get('success'): return None
        for r in j.get('reviews') or []: rs[r.get('recommendationid')] = r
    rs = list(rs.values())
    en = [r for r in rs if r.get('language') == 'english']
    hours = [(r.get('author') or {}).get('playtime_at_review', 0) / 60 for r in rs]
    share = lambda ws: round(100 * sum(1 for r in en if has_word((r.get('review') or '').lower(), ws)) / len(en)) if en else None

    def sample(up):
        pool = [r for r in en if bool(r.get('voted_up')) == up]
        quality = [r for r in pool if len((r.get('review') or '').strip()) >= 100
                   and (r.get('votes_funny') or 0) <= (r.get('votes_up') or 0)]
        ranked = sorted(quality or pool, key=lambda r: -(r.get('votes_up') or 0))
        return [dict(votes=r.get('votes_up') or 0,
                     hours=round(((r.get('author') or {}).get('playtime_at_review') or 0) / 60, 1),
                     text=re.sub(r'\s+', ' ', r.get('review') or '')[:400])
                for r in ranked[:12]]

    return dict(n=len(rs), english_share=round(100 * len(en) / len(rs)) if rs else None,
                playtime_median_h=round(statistics.median(hours), 1) if hours else None,
                guide_share=share(REVIEW_GUIDE), perf_share=share(REVIEW_PERF), positive=sample(True), negative=sample(False))


def forum(net, appid, now):
    """讨论区首页（不含置顶）：标题、回复数、最后回复时间。"""
    h = net.text(FORUM.format(appid), f'讨论区 {appid}')
    if h is None: return None
    topics = []
    for blk in re.split(r'class="forum_topic ', h)[1:]:
        if 'sticky' in blk.split('"', 1)[0]: continue
        name = re.search(r'forum_topic_name[^>]*>(.*?)</div>', blk, re.S)
        if not name: continue
        title = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', name.group(1)))).strip()
        rc = re.search(r'forum_topic_reply_count">.*?>\s*([\d,]+)', blk, re.S)
        ts = re.search(r'data-timestamp="(\d+)"', blk)
        topics.append(dict(title=title, replies=int(rc.group(1).replace(',', '')) if rc else 0,
                           last=dt.datetime.fromtimestamp(int(ts.group(1)), TZ).strftime('%m-%d') if ts else '',
                           week=bool(ts and now.timestamp() - int(ts.group(1)) < 7 * 86400)))
    q = lambda t: '?' in t or re.match(r'(?i)(how|where|what|why|when|can|is|does|do|help|stuck|any)\b', t)
    total = re.search(r'_pagetotal">\s*([\d,]+)', h)
    return dict(topics=topics, total=int(total.group(1).replace(',', '')) if total else None, active_7d=sum(t['week'] for t in topics), questions=sum(1 for t in topics if q(t['title'])))


def suggest(net, name):
    """Google 联想词：输入「游戏名 」后的下拉建议，看里面有没有攻略类词。"""
    norm = lambda x: ' '.join(re.sub(r"['’]", '', x.lower()).replace('-', ' ').split()).strip()
    norm2 = lambda x: ' '.join(re.sub(r'[^\w\s]', ' ', norm(x)).split())
    t = norm2(search_name(name))   # 「Casualties: Unknown」→「casualties unknown」，跟人在搜索框里打的一样
    j = net.json(SUGGEST.format(quote(t + ' ')), f'联想词 {t}')
    if not isinstance(j, list) or len(j) < 2: return None
    sug = [s for s in j[1] if isinstance(s, str)]
    tails = [norm2(s)[len(t):].strip() for s in sug if norm2(s).startswith(t)]
    return dict(query=t, list=sug, with_name=len(tails),
                guide=sorted({w for x in tails for w in GUIDE_WORDS if has_word(x, (w,))}))


def check_domain(net, d, flags):
    """RDAP 查注册情况和注册日期；RDAP 被限额（403/429）后整轮改用 DNS（只能看有没有 NS 记录）。"""
    if not flags.get('rdap'):
        st, body = net.get(RDAP.format(d), tries=1)
        if st == 404: return dict(domain=d, registered=False, via='rdap')
        if st == 200:
            try: ev = {e.get('eventAction'): (e.get('eventDate') or '')[:10] for e in json.loads(body).get('events') or []}
            except ValueError: ev = {}
            return dict(domain=d, registered=True, created=ev.get('registration'), via='rdap')
        flags['rdap'] = f'http {st}' if st else body
    st, body = net.get(DOH.format(d))
    if st == 200:
        try:
            s = json.loads(body).get('Status')
            if s == 3: return dict(domain=d, registered=False, via='dns')
            if s == 0: return dict(domain=d, registered=True, via='dns')
        except ValueError: pass
    return dict(domain=d, registered=None, via='dns', err=f'http {st}' if st else body)


def domains(net, name, flags):
    return [check_domain(net, d, flags) for d in wiki_domains(name)]


# ---------- Roblox ----------

def clean_roblox_name(name):
    """去掉 Roblox 标题里的更新标签、活动标记和 Emoji。"""
    s = re.sub(r'\[[^\]]*\]|\([^)]*\)|【[^】]*】', ' ', unicodedata.normalize('NFKC', name or ''))
    s = ''.join(' ' if unicodedata.category(c) in ('So', 'Sk', 'Cf') or c in '\ufe0f\u20e3' else c for c in s)
    s = re.sub(r'\b(upd|update|new|beta|alpha|event|release|codes)\b\s*\d*', ' ', s, flags=re.I)
    return ' '.join(s.split()).strip(' -–—|:!') or name or ''


def roblox_rows(net, url, label, field):
    data = net.json(url, label)
    if isinstance(data, dict) and isinstance(data.get(field), list):
        return [row for row in data[field] if isinstance(row, dict)]
    if data is not None:
        net.log.append(f'{label}: 缺少 {field} 列表')
    return None


def fetch_roblox(net, o, state, now, flags):
    """抓 Roblox 新星/飙升榜，按创建时间、在线人数和好评率筛选。"""
    sid = uuid.uuid4().hex
    sorts = roblox_rows(net, RBX_SORTS.format(sid), 'Roblox 榜单列表', 'sorts')
    if sorts is None:
        return [], False
    available = {row.get('sortId') for row in sorts if isinstance(row.get('sortId'), str)}
    complete = True
    ids = {}
    for sort_id in RBX_CHARTS:
        if sort_id not in available:
            net.log.append(f'Roblox 榜单列表: 缺少 {sort_id}')
            complete = False
            continue
        rows = roblox_rows(net, RBX_CONTENT.format(sid, sort_id), f'Roblox 榜单 {sort_id}', 'games')
        if rows is None:
            complete = False
            continue
        ids.update((str(row['universeId']), None) for row in rows if row.get('universeId'))

    games, votes = {}, {}
    uids = list(ids)
    for offset in range(0, len(uids), 50):
        batch_ids = uids[offset:offset + 50]
        batch = ','.join(batch_ids)
        for target, url, label in ((games, RBX_GAMES, '详情'), (votes, RBX_VOTES, '投票')):
            rows = roblox_rows(net, url.format(batch), f'Roblox {label}批次 {offset // 50 + 1}', 'data')
            target.update((str(row['id']), row) for row in rows or [] if row.get('id'))
            missing = set(batch_ids) - target.keys()
            if missing:
                if rows is not None:
                    net.log.append(f'Roblox {label}批次 {offset // 50 + 1}: 缺少 {len(missing)} 条记录')
                complete = False
        if offset + 50 < len(uids):
            net.sleep(0.5)

    ledger = state['roblox']
    stamp = now.isoformat(timespec='minutes')
    max_days = getattr(o, 'roblox_max_days', 14)
    min_ccu = getattr(o, 'roblox_min_ccu', 500)
    max_ccu = getattr(o, 'roblox_max_ccu', 15000)
    min_like = getattr(o, 'roblox_min_like', 85)
    candidates = []
    for uid in uids:
        prev = ledger.get(uid, {})
        game = games.get(uid, {})
        ccu = game.get('playing')
        ledger[uid] = dict(prev, name=game.get('name') or prev.get('name', ''), last_seen=stamp,
                           first_seen=prev.get('first_seen') or stamp)
        if not isinstance(ccu, int):
            continue
        prev_ccu = prev.get('last_ccu')
        # 投票接口失败时保留上次基线，以便下轮可以重试本轮的增长信号。
        if uid in votes or prev.get('status') == 'pushed':
            ledger[uid]['last_ccu'] = ccu
        if prev.get('status') == 'pushed' or not min_ccu <= ccu <= max_ccu:
            continue
        created = parse_iso(game.get('created'))
        if created is None or created.tzinfo is None or created > now or not game.get('rootPlaceId'):
            continue
        age_days = (now - created).days
        if age_days > max_days:
            continue

        vote = votes.get(uid, {})
        up, down = vote.get('upVotes') or 0, vote.get('downVotes') or 0
        total = up + down
        if total < 100 or 100 * up < min_like * total:
            continue
        growth = 100 * (ccu - prev_ccu) / prev_ccu if prev_ccu and prev_ccu > 0 else None
        if (growth is None and ccu < max(1000, min_ccu)) or (growth is not None and growth < 20):
            continue

        name = game.get('name') or ''
        clean_name = clean_roblox_name(name)
        if not clean_name:
            continue
        desc = game.get('description') or ''
        community_links = re.findall(
            r'(?i)\b(?:https?://)?(?:www\.)?(?:discord\.gg/|discord\.com/invite/|trello\.com/)[^\s<>]+', desc)
        candidates.append(dict(uid=uid, place_id=game['rootPlaceId'], name=name, clean_name=clean_name,
                               age_days=age_days, ccu=ccu, prev_ccu=prev_ccu,
                               growth=round(growth, 1) if growth is not None else None,
                               like_ratio=round(100 * up / total), total_votes=total,
                               wiki_potential=any(k in f'{name} {desc}'.lower() for k in RBX_WIKI_KW),
                               has_community=bool(community_links), community_links=list(dict.fromkeys(community_links))))

    candidates.sort(key=lambda x: (not x['wiki_potential'], not x['has_community'], -x['ccu']))
    top = candidates[:10]

    def enrich(candidate):
        candidate['suggest'] = suggest(net, candidate['clean_name'])
        candidate['domains'] = domains(net, candidate['clean_name'], flags)

    pmap(enrich, top, 4)
    for candidate in top:
        ledger[candidate['uid']].update(status='pushed', pushed_at=stamp)

    if complete:
        cutoff = now - dt.timedelta(days=14)
        for uid, record in list(ledger.items()):
            seen = parse_iso(record.get('last_seen'))
            if record.get('status') != 'pushed' and (seen is None or seen.tzinfo is None or seen < cutoff):
                del ledger[uid]
    return top, complete


# ---------- 状态 ----------

def load_json(p, default):
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except (OSError, ValueError): return default


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def load_state():
    s = load_json(STATEF, {})
    if not isinstance(s, dict): s = {}
    if 'apps' not in s and 'findnews' not in s:
        # 旧版账本的游戏词可以叫 roblox，新版账本始终有 apps 和 findnews。
        s = dict(findnews=s)
    s.setdefault('apps', {})
    s.setdefault('findnews', {})
    s.setdefault('roblox', {})
    return s


def fresh(cache, now, hours):
    try: return cache and (now - dt.datetime.fromisoformat(cache['at'])).total_seconds() < hours * 3600
    except (KeyError, ValueError, TypeError): return False


# ---------- 主流程 ----------

def run(o, net, now):
    log, today, stamp = net.log, now.date(), now.isoformat(timespec='minutes')
    state = load_state(); apps = state['apps']

    # 1. Steam 榜单
    entries, src_ok, src_err = {}, [], []
    for f, pages, label in SOURCES:
        rank = 0
        for p in range(pages):
            j = net.json(SEARCH.format(f=f, s=p * 50), f'Steam {label} 第 {p + 1} 页')
            rows = parse_search((j or {}).get('results_html'))
            for r in rows:
                rank += 1
                entries.setdefault(r['appid'], dict(r, ranks={}))['ranks'].setdefault(label, rank)
            if not rows: break
        (src_ok if rank else src_err).append(label)
    feed_ok = bool(src_ok)

    # 2. 按榜单上的发售日粗筛
    days_of = lambda iso: (dt.date.fromisoformat(iso) - today).days if iso else None
    need = []
    for a, e in entries.items():
        if apps.get(a, {}).get('status') in ('pushed', 'old', 'excluded'): continue
        rel = parse_date(e['release_raw'])
        d = (rel - today).days if rel else None
        if d is not None and d < -o.max_released_days:
            apps[a] = dict(name=e['name'], status='old', first_seen=stamp); continue
        if d is not None and d > o.early_days: continue
        if d is None and '热门即将发售' not in e['ranks']: continue
        need.append(a)

    # 3. 商店详情（缓存 72 小时）
    def get_details(a):
        c = apps.get(a, {}).get('details')
        if fresh(c, now, DETAILS_TTL_H) and 'nsfw' in c: return c
        net.sleep(o.pace)
        d = details(net, a)
        if d: d['at'] = stamp
        return d
    dets = dict(zip(need, pmap(get_details, need, 2)))

    # 4. 分桶
    pool, excluded = [], []
    for a in need:
        e, d = entries[a], dets[a]
        if not d: continue   # 没拿到详情，下轮再试，不记账
        if d['type'] != 'game':
            apps[a] = dict(name=e['name'], status='excluded', reason=f"不是游戏本体（{d['type']}）", first_seen=stamp); continue
        if d.get('nsfw'):
            name = d['name'] or e['name']
            reason = 'Steam 成人内容分级'
            apps[a] = dict(name=name, status='excluded', reason=reason, first_seen=apps.get(a, {}).get('first_seen', stamp))
            excluded.append(dict(name=name, reason=reason))
            continue
        days = days_of(d['release']) if d['release'] else (lambda r: (r - today).days if r else None)(parse_date(e['release_raw']))
        if days is not None and days < -o.max_released_days:
            apps[a] = dict(name=d['name'], status='old', first_seen=stamp); continue
        if days is not None and days > o.early_days: continue
        filler = set(e['ranks']) == {FILLER}
        bucket = 'window' if days is not None and days <= o.pre_days and not filler else 'early'
        pool.append(dict(appid=a, name=d['name'] or e['name'], days=days, release=d['release'], release_raw=d['release_raw'] or e['release_raw'],
                         ranks=e['ranks'], bucket=bucket, filler=filler, details=d))

    # 5. 在线人数：试玩版取最高的那个，已发售的查正式版
    def get_players(c):
        demo = [x for x in (players(net, i) for i in c['details']['demos'][:3]) if x is not None]
        c['demo_players'] = max(demo) if demo else None
        c['players'] = players(net, c['appid']) if c['days'] is not None and c['days'] <= 0 else None
    pmap(get_players, pool, 8)

    for c in pool:
        hot_demo = (c['demo_players'] or 0) >= o.min_demo
        if c['bucket'] == 'early' and hot_demo:
            c['bucket'], c['early_entry'] = 'window', c['days'] is None or c['days'] > o.pre_days
        if c['filler'] and c['bucket'] == 'window' and not hot_demo: c['bucket'] = 'early'
    heat = lambda c: (-(max(c.get('players') or 0, c.get('demo_players') or 0)), min(c['ranks'].values()))
    window = sorted((c for c in pool if c['bucket'] == 'window'), key=heat)
    cands, deferred = window[:o.max_new], window[o.max_new:]
    watch = sorted((c for c in pool if c['bucket'] == 'early'), key=heat)

    # 6. 补全候选：舆情、联想词、竞品域名
    flags = {}
    def enrich(c):
        released = c['days'] is not None and c['days'] <= 0
        c['hist'] = review_hist(net, c['appid'], today) if released else None
        c['reviews'] = review_texts(net, c['appid']) if released else None
        c['forum'] = forum(net, c['appid'], now)
        c['suggest'] = suggest(net, c['name'])
        c['domains'] = domains(net, c['name'], flags)
        c['flags'] = flag_list(c)
    pmap(enrich, cands, 4)

    # 7. 复查：推过的游戏发售后第 3 到 7 天看一次
    rechecks = []
    for a, s in apps.items():
        d = days_of(s.get('release')) if s.get('status') == 'pushed' else None
        if d is not None and -7 <= d <= -3 and not s.get('rechecked'):
            rechecks.append(dict(appid=a, name=s.get('name'), days=d, players_at_push=s.get('players_at_push'),
                                 demo_at_push=s.get('demo_at_push')))
    def get_recheck(r):
        r['players'] = players(net, r['appid'])
        r['hist'] = review_hist(net, r['appid'], today)
        r['suggest'] = suggest(net, r['name'])
    pmap(get_recheck, rechecks, 4)

    # 8. FindNews 补充（只当线索：名字、首次发现时间，再查联想词和域名）
    fn, fn_ok = [], None
    if not o.no_findnews:
        data = net.json(FINDNEWS, 'FindNews')
        fn_ok = isinstance(data, dict) and isinstance(data.get('items'), list)
        steam_names = {ukey(c['name']) for c in pool} | {ukey(s.get('name')) for s in apps.values()}
        for g in data['items'] if fn_ok else []:
            if not isinstance(g, dict): continue
            name = (g.get('name') or '').strip(); k = ukey(name)
            if not k or k in state['findnews']: continue
            first = parse_iso(g.get('first'))
            new = bool(first and (now - first).total_seconds() <= 48 * 3600) and k not in steam_names
            state['findnews'][k] = dict(name=name, first_seen=stamp, pushed=new)
            if new: fn.append(dict(name=name, score=g.get('score') or 0, first=first.astimezone(TZ).strftime('%m-%d %H:%M')))
        fn.sort(key=lambda x: -x['score'])
        def fn_enrich(x):
            x['suggest'] = suggest(net, x['name']); x['domains'] = domains(net, x['name'], flags)
        pmap(fn_enrich, fn[:10], 4)

    roblox, roblox_ok = [], None
    if not getattr(o, 'no_roblox', True):
        roblox, roblox_ok = fetch_roblox(net, o, state, now, flags)

    res = dict(at=now.strftime('%Y-%m-%d %H:%M'), feed_ok=feed_ok, sources_ok=src_ok, sources_err=src_err,
               rdap_blocked=flags.get('rdap'), over_budget=net.over, findnews_ok=fn_ok, log=log,
               min_demo=o.min_demo, candidates=cands, deferred=[slim(c) for c in deferred],
               watch=[slim(c) for c in watch[:15]], watch_total=len(watch), rechecks=rechecks, findnews=fn,
               roblox=roblox, roblox_ok=roblox_ok, excluded=excluded)
    out = out_dir(now, o.slot)
    save_json(os.path.join(out, 'games.json'), res)
    md = render(res)
    with open(os.path.join(out, 'report.md'), 'w', encoding='utf-8') as f: f.write(md)

    # 9. 报告写完才记账：中途被打断，下次还会再出
    if feed_ok and not o.no_save:
        for c in cands:
            apps[c['appid']] = dict(name=c['name'], status='pushed', first_seen=apps.get(c['appid'], {}).get('first_seen', stamp),
                                    pushed_at=stamp, release=c['release'], players_at_push=c.get('players'),
                                    demo_at_push=c.get('demo_players'), details=c['details'])
        for c in deferred + watch:
            apps[c['appid']] = dict(apps.get(c['appid'], {}), name=c['name'], status='watch', release=c['release'],
                                    details=c['details'], first_seen=apps.get(c['appid'], {}).get('first_seen', stamp))
        for r in rechecks: apps[r['appid']]['rechecked'] = stamp
        save_json(STATEF, state)
    return (0 if feed_ok else 1), res, md, out


def parse_iso(s):
    try: return dt.datetime.fromisoformat((s or '').replace('Z', '+00:00'))
    except ValueError: return None


def slim(c):
    return {k: c.get(k) for k in ('appid', 'name', 'days', 'release_raw', 'ranks', 'demo_players', 'players')}


def flag_list(c):
    d, out = c['details'], []
    who = ' / '.join(d['developers'] + d['publishers']).lower()
    if any(re.search(r'\b' + re.escape(b) + r'\b', who) for b in BIG): out.append('大厂或大 IP 嫌疑，核实后决定是否排除')
    h = c.get('hist') or {}
    if h.get('total', 0) >= 20 and h.get('positive') is not None and h['positive'] < 40: out.append(f"好评率 {h['positive']}%，低于 40%")
    s = c.get('suggest')
    if s is not None and not s['guide'] and c['days'] is not None and c['days'] <= -3:
        out.append('发售 3 天以上，联想词里还没有攻略类词（可能玩家多但没人搜）')
    if not d.get('english'): out.append('不支持英语')
    return out


def out_dir(now, slot):
    base = os.path.join(HOME, 'out', now.strftime('%Y%m%d') + '_' + (slot or now.strftime('%H%M')))
    out, k = base, 2
    while os.path.exists(out): out, k = f'{base}_{k}', k + 1
    os.makedirs(out)
    return out


# ---------- 播报草稿 ----------

def stage(c):
    d = c['days']
    if d is None: return f"发售日未定（{c.get('release_raw') or '无'}）"
    when = (c.get('release') or '')[5:]
    return f'发售前 {d} 天（{when}）' if d > 0 else f'今天发售' if d == 0 else f'已发售 {-d} 天（{when}）'


def heat_line(c):
    p = []
    if c.get('players') is not None: p.append(f"在线 {c['players']:,} 人")
    if c.get('demo_players') is not None: p.append(f"试玩版在线 {c['demo_players']:,} 人")
    elif c['details']['demos']: p.append('有试玩版，在线人数没查到')
    p += [f'{k}第 {v} 名' for k, v in c['ranks'].items() if k != FILLER]
    h = c.get('hist')
    if h and h['total']: p.append(f"评测 {h['total']:,} 条（好评 {h['positive']}%）")
    f = c.get('forum')
    if f: p.append((f"讨论区 {f['total']:,} 帖，" if f.get('total') else '讨论区') + f"首页近 7 天活跃 {f['active_7d']} 帖")
    return '；'.join(p) or '没有可用数据'


def trend_line(c):
    h = c.get('hist')
    if h and h['last3_daily'] is not None:
        s = f"评测近 3 天日均 {h['last3_daily']:g} 条"
        if h['prev4_daily'] is not None: s += f"，之前 4 天日均 {h['prev4_daily']:g} 条"
        if h['recent7_positive'] is not None: s += f"，近 7 天好评 {h['recent7_positive']}%"
        return s
    return '还没有评测走势，看讨论区和 X 上的讨论（待补）'


def sentiment_lines(c):
    L = ['- 舆情：待写']
    r = c.get('reviews')
    if r and r['n']:
        L.append(f"  - 评测统计（{r['n']} 条）：英文占 {r['english_share']}%，评测时游戏时长中位数 {r['playtime_median_h']:g} 小时，"
                 f"提到攻略类词 {r['guide_share']}%，抱怨性能 {r['perf_share']}%（好评、差评样本在 games.json）")
    elif c['days'] is not None and c['days'] <= 0:
        L.append('  - 评测原文：没拿到（见报告开头），去商店页看')
    f = c.get('forum')
    if f and f['topics']:
        top = sorted(f['topics'], key=lambda t: -t['replies'])[:5]
        L.append(f"  - 讨论区热帖（首页 {len(f['topics'])} 帖，提问 {f['questions']} 帖）：" + '；'.join(f"{t['title']}（{t['replies']}）" for t in top))
    elif f is not None:
        L.append('  - 讨论区：首页没有帖子')
    return L


def search_line(s):
    if s is None: return '- 有没有人搜：联想词没查到'
    if not s['list']: return f"- 有没有人搜：输入「{s['query']} 」没有任何联想词"
    g = '、'.join(s['guide']) if s['guide'] else '还没有'
    return f"- 有没有人搜：联想词 {len(s['list'])} 条（带游戏名 {s['with_name']} 条），攻略类：{g}；前几条：" + '、'.join(s['list'][:5])


def comp_line(ds):
    if not ds: return '- 竞争：名字不是英文，域名没查；竞品站待查'
    reg = [d for d in ds if d['registered']]
    unk = [d for d in ds if d['registered'] is None]
    free = [d['domain'] for d in ds if d['registered'] is False]
    s = f'- 竞争：{len(ds)} 个 .wiki 域名 {len(reg)} 个已注册'
    if reg: s += '：' + '、'.join(d['domain'] + (f"（{d['created']}）" if d.get('created') else '') for d in reg)
    if free: s += f"；空闲：{'、'.join(free)}"
    if unk: s += f'；{len(unk)} 个没查到'
    if any(d['via'] == 'dns' for d in ds): s += '（DNS 判断：注册了但没解析的会算成空闲，注册前再确认）'
    return s + '；竞品站内容深度待查'


def roblox_lines(res):
    lines = ['', '五、Roblox 飙升新游（快闪 Wiki 候选）']
    candidates = res.get('roblox') or []
    if not candidates:
        status = res.get('roblox_ok')
        lines.append({True: '无', False: '数据不全，无法判断', None: '本轮未采集'}[status])
    for game in candidates:
        tags = []
        if game['wiki_potential']: tags.append('高 Wiki 潜质')
        if game['has_community']: tags.append('简介有 Discord/Trello 线索')
        tag = f"[{' · '.join(tags)}] " if tags else ''
        growth = game['growth']
        change = f'（较上轮 +{growth:g}%）' if growth is not None else '（无可比的在线人数快照）'
        lines += ['', f"**{tag}{game['name']}**", '',
                  f"- 阶段：创建 {game['age_days']} 天（清洗词根：{game['clean_name']}）",
                  f"- 热度：在线 {game['ccu']:,} 人{change}；好评 {game['like_ratio']}%（{game['total_votes']:,} 票）",
                  '- 轻量舆情：待检（YouTube 近 7 天视频和评论区、官方社区）',
                  search_line(game.get('suggest')),
                  comp_line(game.get('domains')),
                  f"- 链接：{RBX_URL.format(game['place_id'])}"]
    return lines


def render(res):
    L = [f"**游戏新词雷达 · {res['at']}**",
         f"新游戏 {len(res['candidates'])} · 复查 {len(res['rechecks'])} · 早期观察 {res['watch_total']} · "
         f"FindNews 补充 {len(res['findnews'])} · Roblox 新游 {len(res.get('roblox') or [])}"]
    if not res['feed_ok']:
        L += ['', '⚠ 这轮 Steam 榜单全部没拿到，不代表没有新游戏：' + '；'.join(res['log'][:6])]
    warn = []
    if res['feed_ok'] and res['sources_err']: warn.append(f"Steam 榜单没拿到：{'、'.join(res['sources_err'])}，这部分游戏可能漏掉")
    if any(c.get('reviews') is None and c['days'] is not None and c['days'] <= 0 for c in res['candidates']):
        warn.append('Steam 评测原文接口没拿到（当前网络被重定向或拦截），评测只有数量和好评率')
    if res['rdap_blocked']: warn.append(f"RDAP 查域名被拒（{res['rdap_blocked']}），改用 DNS 判断，没有注册日期")
    if res['over_budget']: warn.append('超过本轮时间上限，部分数据没查')
    if res['findnews_ok'] is False: warn.append('FindNews 没拿到，补充线索缺失')
    if res.get('roblox_ok') is False: warn.append('Roblox 榜单、详情或投票数据未完整取得，候选可能有遗漏')
    L += [''] + [f'⚠ {w}' for w in warn] if warn else []
    if not res['feed_ok']:
        if res.get('roblox_ok') is not None:
            L += roblox_lines(res)
        return '\n'.join(L) + '\n'

    L += ['', '一、新游戏']
    if not res['candidates']: L.append('无')
    for i, c in enumerate(res['candidates'], 1):
        d = c['details']
        info = ' · '.join(x for x in [d['price'], ', '.join(d['genres'][:4]),
                                      '开发 ' + '、'.join(d['developers'][:2]) if d['developers'] else '',
                                      '发行 ' + '、'.join(d['publishers'][:2]) if d['publishers'] else ''] if x)
        L += ['', f"**{i}. {c['name']}**", '',
              f"- 阶段：{stage(c)}" + ('｜试玩版够热，可提前进场' if c.get('early_entry') else ''),
              f"- 热度：{heat_line(c)}",
              f"- 趋势：{trend_line(c)}",
              f"- 基本信息：{info or '无'}",
              *sentiment_lines(c),
              search_line(c.get('suggest')),
              comp_line(c.get('domains')),
              '- 打分：待评']
        if c.get('flags'): L.append('- ⚑ ' + '；'.join(c['flags']))
        L.append(f"- 链接：{STORE.format(c['appid'])} · {STEAMDB.format(c['appid'])}")
    if res['deferred']:
        L += ['', f"本轮只展开前 {len(res['candidates'])} 个，另有 {len(res['deferred'])} 个下轮再看：" + '、'.join(c['name'] for c in res['deferred'])]

    L += ['', '二、复查（发售后第 3 到 7 天）']
    if not res['rechecks']: L.append('无')
    for r in res['rechecks']:
        h, then = r.get('hist') or {}, r.get('players_at_push') or r.get('demo_at_push')
        parts = [f"已发售 {-r['days']} 天", f"在线 {r['players']:,} 人" if r.get('players') is not None else '在线人数没查到']
        if then: parts.append(f'推的时候 {then:,} 人')
        if h.get('total'): parts.append(f"评测 {h['total']:,} 条（好评 {h['positive']}%），近 3 天日均 {h['last3_daily']:g} 条")
        s = r.get('suggest')
        if s: parts.append('联想词攻略类：' + ('、'.join(s['guide']) or '还没有'))
        L.append(f"- {r['name']}：" + '，'.join(parts) + '｜加码还是停手：待评')

    L += ['', f"三、早期观察（15 到 49 天内发售或发售日未定，试玩版不到 {res['min_demo']} 人）"]
    if not res['watch']: L.append('无')
    for c in res['watch']:
        demo = f"试玩版 {c['demo_players']:,} 人" if c.get('demo_players') is not None else '没有试玩版数据'
        when = f"{c['days']} 天后发售" if c['days'] is not None else f"发售日 {c['release_raw'] or '未定'}"
        L.append(f"- {c['name']}：{when}，{demo}，" + '、'.join(f'{k}第 {v} 名' for k, v in c['ranks'].items()))
    if res['watch_total'] > len(res['watch']): L.append(f"- 另有 {res['watch_total'] - len(res['watch'])} 个没列出")

    L += ['', '四、FindNews 补充（不在 Steam 榜单上的新词线索）']
    if not res['findnews']: L.append('无')
    for x in res['findnews'][:10]:
        s = x.get('suggest')
        bits = [f"首次发现 {x['first']}", f"score {x['score']}"]
        if s: bits.append('联想词攻略类：' + ('、'.join(s['guide']) or '还没有'))
        if x.get('domains'): bits.append(f"域名已注册 {sum(1 for d in x['domains'] if d['registered'])}/{len(x['domains'])}")
        L.append(f"- {x['name']}：" + '，'.join(bits))
    if len(res['findnews']) > 10: L.append(f"- 另有 {len(res['findnews']) - 10} 个 score 更低的，见 games.json")

    L += roblox_lines(res)
    L += ['', '六、没推的']
    L += [f"- {item['name']}：{item['reason']}" for item in res.get('excluded', [])] or ['无']
    return '\n'.join(L) + '\n'


def main():
    env = lambda k, v: type(v)(os.environ.get(k, v))
    ap = argparse.ArgumentParser()
    ap.add_argument('--slot', default='', help='时段标签，例如 morning / evening；默认用当前时分')
    ap.add_argument('--pre-days', type=int, default=env('RADAR_PRE_DAYS', 14), help='几天内发售的直接进新游戏')
    ap.add_argument('--early-days', type=int, default=env('RADAR_EARLY_DAYS', 49), help='几天内发售的看试玩版热度')
    ap.add_argument('--max-released-days', type=int, default=env('RADAR_MAX_RELEASED_DAYS', 14), help='已发售超过几天算老词')
    ap.add_argument('--min-demo', type=int, default=env('RADAR_MIN_DEMO', 250), help='试玩版在线人数门槛')
    ap.add_argument('--max-new', type=int, default=env('RADAR_MAX_NEW', 15), help='一轮最多展开几个新游戏')
    ap.add_argument('--budget', type=int, default=600, help='一轮最多跑几秒')
    ap.add_argument('--pace', type=float, default=0.4, help='商店详情接口每次请求间隔几秒')
    ap.add_argument('--no-findnews', action='store_true', help='不抓 FindNews')
    ap.add_argument('--no-roblox', action='store_true', help='不抓 Roblox')
    ap.add_argument('--roblox-max-days', type=int, default=env('RADAR_RBX_DAYS', 14), help='Roblox 新游创建天数上限')
    ap.add_argument('--roblox-min-ccu', type=int, default=env('RADAR_RBX_MIN_CCU', 500), help='Roblox 最低在线人数')
    ap.add_argument('--roblox-max-ccu', type=int, default=env('RADAR_RBX_MAX_CCU', 15000), help='Roblox 最高在线人数')
    ap.add_argument('--roblox-min-like', type=int, default=env('RADAR_RBX_MIN_LIKE', 85), help='Roblox 最低好评率（百分比）')
    ap.add_argument('--no-save', action='store_true', help='试跑：不写账本')
    o = ap.parse_args()
    log = []
    code, res, _, out = run(o, Net(log, budget=o.budget), dt.datetime.now(TZ))
    print(f"radar: feed_ok={res['feed_ok']}，新游戏 {len(res['candidates'])}，复查 {len(res['rechecks'])}，"
          f"早期观察 {res['watch_total']}，FindNews 补充 {len(res['findnews'])}，Roblox 新游 {len(res['roblox'])}"
          + ('（试跑，没写账本）' if o.no_save else ''))
    print('结果：' + out)
    for l in log[:40]: print(' ', l)
    if len(log) > 40: print(f'  …… 另有 {len(log) - 40} 条，见 games.json 的 log')
    sys.exit(code)


if __name__ == '__main__':
    main()
