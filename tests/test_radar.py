#!/usr/bin/env python3
"""离线测试：假的 Steam / Google / RDAP / FindNews 接口，不联网。"""
import os, sys, json, tempfile, unittest, importlib, argparse, datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
NOW = dt.datetime(2026, 11, 20, 9, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))
TODAY = NOW.date()


def day(n):
    return (TODAY + dt.timedelta(days=n)).strftime('%b %-d, %Y')


def row(appid, name, rel):
    return (f'<a href="https://store.steampowered.com/app/{appid}/x/" data-ds-appid="{appid}" class="search_result_row">'
            f'<div><span class="title">{name}</span></div><div class="search_released responsive_secondrow">{rel}</div></a>')


def page(*rows):
    return json.dumps(dict(success=1, results_html=''.join(rows)))


def appdetails(appid, name, rel, demos=(), typ='game', dev='Tiny Studio'):
    return json.dumps({appid: dict(success=True, data=dict(
        type=typ, name=name, release_date=dict(coming_soon=True, date=rel), demos=[dict(appid=d) for d in demos],
        developers=[dev], publishers=[dev], is_free=False, price_overview=dict(final_formatted='$9.99'),
        genres=[dict(description='Indie')], supported_languages='English, French', recommendations=dict(total=50)))})


def histo(days_back=10, per_day=(8, 2)):
    base = dt.datetime.combine(TODAY, dt.time(), dt.timezone.utc)
    recent = [dict(date=int((base - dt.timedelta(days=i)).timestamp()), recommendations_up=per_day[0],
                   recommendations_down=per_day[1]) for i in range(days_back, -1, -1)]
    return json.dumps(dict(success=1, results=dict(rollups=recent, recent=recent, rollup_type='day')))


FORUM_HTML = ('<span id="forum_General_1_pagetotal">551</span>'
              '<div data-panel="{}" role="button" class="forum_topic  sticky" data-x="1"><div class="forum_topic_name ">Read me</div></div>'
              '<div data-panel="{}" role="button" class="forum_topic  unread"><div class="forum_topic_reply_count"><img src="x"> 12 </div>'
              f'<div class="forum_topic_lastpost" data-timestamp="{int(NOW.timestamp()) - 3600}"></div>'
              '<div class="forum_topic_name ">How do I beat the first boss?</div></div>')


class Fake:
    """按 URL 片段匹配假响应；routes 里的值可以是 (状态码, 正文) 或函数。"""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, timeout):
        self.calls.append(url)
        for k, v in self.routes.items():
            if k in url: return v(url) if callable(v) else v
        return 404, ''


def base_routes(**extra):
    r = {
        'filter=popularcomingsoon&infinite=1&start=0': (200, page(
            row(1, 'Soon Game', day(5)), row(2, 'Far Hot Demo', day(30)), row(3, 'Far Cold Demo', day(30)),
            row(4, 'Soundtrack Thing', day(3)))),
        'filter=popularnew&infinite=1&start=0': (200, page(row(5, 'Fresh Release', day(-4)), row(6, 'Ancient', 'Mar 25, 2013'))),
        'filter=comingsoon&infinite=1&start=0': (200, page(row(7, 'Filler Cold', day(2)), row(1, 'Soon Game', day(5)))),
        'appids=1&': (200, appdetails(1, 'Soon Game', day(5), demos=[101])),
        'appids=2&': (200, appdetails(2, 'Far Hot Demo: Deluxe', day(30), demos=[201])),
        'appids=3&': (200, appdetails(3, 'Far Cold Demo', day(30), demos=[301])),
        'appids=4&': (200, appdetails(4, 'Soundtrack Thing', day(3), typ='music')),
        'appids=5&': (200, appdetails(5, 'Fresh Release', day(-4), dev='Ubisoft')),
        'appids=7&': (200, appdetails(7, 'Filler Cold', day(2))),
        'appid=101': (200, '{"response":{"player_count":120,"result":1}}'),
        'appid=201': (200, '{"response":{"player_count":900,"result":1}}'),
        'appid=301': (200, '{"response":{"player_count":40,"result":1}}'),
        'appid=5': (200, '{"response":{"player_count":3000,"result":1}}'),
        'appid=1': (200, '{"response":{"result":42}}'),
        'appreviewhistogram/5': (200, histo()),
        'appreviews/': (302, ''),                                    # 评测原文被拦截，跟真实网络一样
        'steamcommunity.com/app/': (200, FORUM_HTML),
        'suggestqueries': lambda u: (200, json.dumps(['x', ['soon game guide', 'soon game wiki', 'soon game release date']])),
        'rdap.nic.wiki/domain/soongame.wiki': (200, json.dumps(dict(events=[dict(eventAction='registration', eventDate='2026-11-01T00:00:00Z')]))),
        'rdap.nic.wiki': (404, ''),
        'findnews.me': (200, json.dumps(dict(items=[
            dict(name='Soon Game', first=(NOW - dt.timedelta(hours=2)).isoformat(), score=9),
            dict(name='Only On FindNews', first=(NOW - dt.timedelta(hours=2)).isoformat(), score=7),
            dict(name='Old FindNews', first=(NOW - dt.timedelta(days=9)).isoformat(), score=7)]))),
    }
    r.update(extra)
    return r


def opts(**kw):
    o = dict(slot='t', pre_days=14, early_days=49, max_released_days=14, min_demo=500, max_new=15, pace=0,
             no_findnews=False, no_save=False)
    o.update(kw)
    return argparse.Namespace(**o)


class Radar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['AGENT_HOME'] = self.tmp.name
        import game_radar
        self.g = importlib.reload(game_radar)

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, routes, **kw):
        log = []
        fake = Fake(routes)
        net = self.g.Net(log, fetch=fake, sleep=lambda s: None)
        code, res, md, out = self.g.run(opts(**kw), net, NOW)
        return code, res, md, out, fake

    def names(self, xs):
        return [x['name'] for x in xs]

    def test_buckets(self):
        code, res, md, out, _ = self.go(base_routes())
        self.assertEqual(code, 0)
        # 在线人数高的在前：Fresh 3000（已发售）、Far Hot 900（试玩版够热，提前进场）、Soon 120
        self.assertEqual(self.names(res['candidates']), ['Fresh Release', 'Far Hot Demo: Deluxe', 'Soon Game'])
        hot = res['candidates'][1]
        self.assertTrue(hot['early_entry'])
        self.assertEqual(self.names(res['watch']), ['Far Cold Demo', 'Filler Cold'])   # 试玩版不够热 / 只在「按发售日」
        self.assertIn('可提前进场', md)
        self.assertNotIn('Soundtrack', md)        # 不是游戏本体
        self.assertNotIn('Ancient', md)           # 老游戏
        seen = json.loads(Path(self.tmp.name, 'state/seen.json').read_text(encoding='utf-8'))['apps']
        self.assertEqual(seen['4']['status'], 'excluded')
        self.assertEqual(seen['6']['status'], 'old')
        self.assertEqual(seen['3']['status'], 'watch')
        self.assertEqual(seen['1']['status'], 'pushed')

    def test_report_fields(self):
        _, res, md, _, _ = self.go(base_routes())
        self.assertIn('- 阶段：已发售 4 天', md)
        self.assertIn('在线 3,000 人', md)
        self.assertIn('评测 110 条（好评 80%）', md)
        self.assertIn('评测近 3 天日均 10 条，之前 4 天日均 10 条', md)
        self.assertIn('⚠ Steam 评测原文接口没拿到', md)
        self.assertIn('How do I beat the first boss?（12）', md)
        self.assertNotIn('Read me', md)           # 置顶帖不算
        self.assertIn('讨论区 551 帖', md)
        self.assertIn('攻略类：guide、wiki', md)
        self.assertIn('soongame.wiki（2026-11-01）', md)
        self.assertIn('- 打分：待评', md)
        self.assertIn('大厂或大 IP 嫌疑', md)       # Fresh Release 是 Ubisoft
        self.assertIn('Only On FindNews', md)
        self.assertNotIn('Old FindNews', md)
        self.assertEqual(md.count('Soon Game'), 1)                 # FindNews 里同名的不重复列
        self.assertNotIn('GPTs', md)

    def test_dedup_and_no_overwrite(self):
        _, _, _, out1, _ = self.go(base_routes())
        code, res, md, out2, fake = self.go(base_routes())
        self.assertEqual(res['candidates'], [])                    # 推过的不再出
        self.assertEqual(self.names(res['watch']), ['Far Cold Demo', 'Filler Cold'])   # 观察中的每轮再看
        self.assertNotEqual(out1, out2)                            # 第二次不覆盖第一次的报告
        self.assertTrue(Path(out1, 'report.md').exists())
        self.assertFalse(any('appids=3&' in u for u in fake.calls))   # 商店详情走缓存

    def test_watch_promoted_when_demo_heats_up(self):
        self.go(base_routes())
        _, res, _, _, _ = self.go(base_routes(**{'appid=301': (200, '{"response":{"player_count":800,"result":1}}')}))
        self.assertEqual(self.names(res['candidates']), ['Far Cold Demo'])

    def test_feed_failure(self):
        routes = {k: v for k, v in base_routes().items() if 'filter=' not in k}
        code, res, md, _, _ = self.go(routes)
        self.assertEqual(code, 1)
        self.assertIn('不代表没有新游戏', md)
        self.assertFalse(Path(self.tmp.name, 'state/seen.json').exists())

    def test_no_save(self):
        self.go(base_routes(), no_save=True)
        self.assertFalse(Path(self.tmp.name, 'state/seen.json').exists())

    def test_rdap_blocked_falls_back_to_dns(self):
        r = base_routes(**{'rdap.nic.wiki': (403, 'Number of allowed queries exceeded'),
                           'dns.google': lambda u: (200, json.dumps(dict(Status=0 if 'name=soongame.wiki' in u else 3)))})
        r.pop('rdap.nic.wiki/domain/soongame.wiki')
        _, res, md, _, fake = self.go(r, no_findnews=True)
        self.assertIn('RDAP 查域名被拒', md)
        self.assertIn('DNS 判断', md)
        self.assertEqual(sum('rdap.nic.wiki' in u for u in fake.calls), 1)   # 被拒一次后不再试
        soon = next(c for c in res['candidates'] if c['name'] == 'Soon Game')
        self.assertEqual([d['domain'] for d in soon['domains'] if d['registered']], ['soongame.wiki'])

    def test_max_new_defers_rest(self):
        _, res, md, _, _ = self.go(base_routes(), max_new=1)
        self.assertEqual(self.names(res['candidates']), ['Fresh Release'])
        self.assertIn('另有 2 个下轮再看', md)
        _, res, _, _, _ = self.go(base_routes(), max_new=5)
        self.assertEqual(self.names(res['candidates']), ['Far Hot Demo: Deluxe', 'Soon Game'])

    def test_recheck_once(self):
        self.go(base_routes())
        later = lambda: self.g.run(opts(), self.g.Net([], fetch=Fake(base_routes()), sleep=lambda s: None), NOW + dt.timedelta(days=8))
        code, res, md, _ = later()
        self.assertEqual(self.names(res['rechecks']), ['Soon Game'])   # 11-25 发售，8 天后是 11-28 = 发售后第 3 天
        self.assertIn('推的时候 120 人', md)
        _, res, _, _ = later()
        self.assertEqual(res['rechecks'], [])

    def test_old_findnews_ledger_migrates(self):
        Path(self.tmp.name, 'state').mkdir()
        Path(self.tmp.name, 'state/seen.json').write_text(json.dumps({'onlyonfindnews': {'name': 'Only On FindNews'}}))
        _, res, _, _, _ = self.go(base_routes())
        self.assertEqual(res['findnews'], [])


class Parsing(unittest.TestCase):
    def setUp(self):
        import game_radar
        self.g = game_radar

    def test_domains(self):
        ds = self.g.wiki_domains('Casualties: Unknown')
        self.assertIn('casualties-unknown.wiki', ds)                # 全名
        self.assertIn('casualtiesguides.wiki', ds)                  # 主标题
        self.assertIn('pokemon-rush.wiki', self.g.wiki_domains('Pokémon Rush'))
        self.assertIn('zombies-arent-real.wiki', self.g.wiki_domains("Zombies Aren't Real"))
        self.assertEqual(len(self.g.wiki_domains('Hades')), 6)       # 单个词时带连字符的和第一个重复
        self.assertEqual(self.g.search_name('Casualties: Unknown'), 'Casualties: Unknown')
        self.assertEqual(self.g.search_name('Melvor Idle 2: A RuneScape Adventure'), 'Melvor Idle 2')

    def test_suggest_ignores_punctuation(self):
        net = self.g.Net([], fetch=lambda u, t: (200, json.dumps(['x', ['casualties unknown wiki', "zombies arent real codes", 'other']])))
        s = self.g.suggest(net, 'Casualties: Unknown')
        self.assertEqual((s['query'], s['with_name'], s['guide']), ('casualties unknown', 1, ['wiki']))
        self.assertEqual(self.g.suggest(net, "Zombies Aren't Real")['guide'], ['codes'])
        self.assertEqual(self.g.wiki_domains('世界之外'), [])

    def test_dates(self):
        self.assertEqual(self.g.parse_date('Nov 24, 2026'), dt.date(2026, 11, 24))
        self.assertEqual(self.g.parse_date('24 Nov, 2026'), dt.date(2026, 11, 24))
        self.assertIsNone(self.g.parse_date('Q1 2027'))
        self.assertIsNone(self.g.parse_date('Coming soon'))


if __name__ == '__main__':
    unittest.main()
