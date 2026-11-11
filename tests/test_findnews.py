#!/usr/bin/env python3
"""离线测试：用临时生成的 games.json 跑 findnews_games.py，不联网。"""
import os, sys, json, subprocess, tempfile, unittest, importlib.util, datetime as dt
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'findnews_games.py'

def ago(h):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=h)).isoformat()

def games(*items):
    return dict(updated=ago(0.5), items=[dict(name=n, series=s, first=ago(h), last=ago(0), score=sc, sightings=k)
                                         for n, s, h, sc, k in items])

RISING = [0] * 30 + [20, 40, 60, 80, 90, 95, 100]
FADING = [100, 80] + [5] * 35


class FindNewsGames(unittest.TestCase):
    def run_once(self, home, data, *args):
        f = Path(home, 'in.json'); f.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        env = dict(os.environ, AGENT_HOME=home, PYTHONUTF8='1')
        p = subprocess.run([sys.executable, str(SCRIPT), '--slot', 't', '--file', str(f), *args],
                           env=env, capture_output=True, text=True, encoding='utf-8')
        out = next(Path(home, 'out').glob('*_t'))
        return p.returncode, json.loads((out / 'games.json').read_text(encoding='utf-8')), \
            (out / 'report.md').read_text(encoding='utf-8')

    def test_fresh_only_then_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            data = games(('Fresh Game', RISING, 3, 12, 1), ('世界之外', FADING, 10, 5, 2),
                         ('Old Game', RISING, 24 * 10, 20, 9))
            rc, j, md = self.run_once(d, data)
            self.assertEqual(rc, 0)
            self.assertEqual([x['name'] for x in j['candidates']], ['Fresh Game', '世界之外'])  # 按 score 排
            self.assertEqual(j['candidates'][0]['trend'], '上升')
            self.assertEqual(j['candidates'][1]['trend'], '回落')
            self.assertIn('**1. Fresh Game**', md)
            self.assertNotIn('Old Game', md)                       # 太早发现的只记账，不出候选
            self.assertEqual(len(json.loads(Path(d, 'state/seen.json').read_text(encoding='utf-8'))), 3)

            data['items'][0]['sightings'] = 3                     # 又被 FindNews 发现
            rc, j, md = self.run_once(d, data)
            self.assertEqual(j['candidates'], [])
            self.assertEqual([x['name'] for x in j['recurring']], ['Fresh Game'])
            self.assertIn('一、新游戏\n无', md)

    def test_min_score(self):
        with tempfile.TemporaryDirectory() as d:
            rc, j, _ = self.run_once(d, games(('A', RISING, 1, 12, 1), ('B', RISING, 1, 5, 1)), '--min-score', '8')
            self.assertEqual([x['name'] for x in j['candidates']], ['A'])

    def test_bad_feed_is_failure(self):
        with tempfile.TemporaryDirectory() as d:
            rc, j, md = self.run_once(d, {'oops': 1})
            self.assertEqual(rc, 1)
            self.assertFalse(j['feed_ok'])
            self.assertIn('不代表没有新游戏', md)
            self.assertFalse(Path(d, 'state/seen.json').exists())  # 失败时不动账本

    def test_offline_skips_trends(self):
        with tempfile.TemporaryDirectory() as d:
            rc, j, md = self.run_once(d, games(('A', RISING, 1, 12, 1)))
            self.assertEqual(rc, 0)
            self.assertEqual(j['trends']['calls'], 0)
            self.assertIn('- 热度：未测（离线调试，没查 Trends）', md)
            self.assertNotIn('⚠', md)                              # 主动不查不算故障


spec = importlib.util.spec_from_file_location('fg', SCRIPT)
fg = importlib.util.module_from_spec(spec); spec.loader.exec_module(fg)

def chart(**cols):
    """假 Trends 图：7 天每小时一个点，cols 给每个词一个常数或 (前 6 天, 最后 24h)。"""
    end = dt.datetime(2026, 11, 11, tzinfo=dt.timezone.utc)
    times = [end - dt.timedelta(hours=h) for h in range(168, 0, -1)]
    cut = times[-1] - dt.timedelta(hours=24)                      # 跟脚本一样从最后一个点往回数
    val = lambda v, t: v if not isinstance(v, tuple) else (v[0] if t <= cut else v[1])
    return times, {k: [float(val(v, t)) for t in times] for k, v in cols.items()}


class GptsScoring(unittest.TestCase):
    def test_ratio_drops_last_24h(self):
        sc, g = fg.vs_gpts(*chart(GPTs=50, A=(10, 100)), ['A'])
        self.assertEqual(g, 50)
        self.assertEqual(sc['A']['ratio'], 0.2)                    # 最后 24h 的 100 不进均值
        self.assertEqual(sc['A']['last24'], 2)

    def test_split_and_failures(self):
        cands = [dict(name=n, score=s) for n, s in [('Big', 1), ('Tiny', 9), ('Lost', 5)]]
        def fake(kws):
            if 'Lost' in kws: raise RuntimeError('429 Too Many Requests')
            return chart(GPTs=50, Big=40, Tiny=1)
        log = []
        calls = fg.score_trends(cands[:2], log, fetch=fake, sleep=lambda s: None)
        fg.score_trends(cands[2:], log, fetch=fake, sleep=lambda s: None, tries=2)
        self.assertEqual(calls, 1)
        keep, watch = fg.split(cands, 0.05)
        self.assertEqual([x['name'] for x in keep], ['Big', 'Lost'])  # 测到的在前，没测到的照样留着
        self.assertEqual(cands[2]['gpts']['err'], 'Trends 没查到')
        self.assertEqual([x['name'] for x in watch], ['Tiny'])       # 1/50 = 0.02 < 0.05
        self.assertEqual(len(log), 2)

    def test_squashed_gpts_requeries_small(self):
        cands = [dict(name='Huge', score=1), dict(name='Small', score=1)]
        asked = []
        def fake(kws):
            asked.append(kws)
            return chart(GPTs=1, Huge=100, Small=2) if len(kws) == 3 else chart(GPTs=50, Small=5)
        fg.score_trends(cands, [], fetch=fake, sleep=lambda s: None)
        self.assertEqual(asked, [['Huge', 'Small', 'GPTs'], ['Small', 'GPTs']])
        self.assertEqual(cands[0]['gpts']['ratio'], 100)
        self.assertEqual(cands[1]['gpts']['ratio'], 0.1)

    def test_render_heat_and_warning(self):
        x = dict(name='A', score=3, sightings=1, fn_first='2026-11-11 08:00', trend='上升', trends_url='u',
                 gpts=dict(ratio=0.3, last24=0.5, trend='在涨'))
        y = dict(x, name='B', gpts=dict(err='Trends 没查到'))
        md = fg.render(dict(at='t', feed_ok=True, stale=False, candidates=[x, y], watch=[], recurring=[],
                            min_gpts=0.05, trends=dict(missed=1, note='Trends 没查到')))
        self.assertIn('⚠ Google Trends 有 1 个游戏没测到', md)
        self.assertIn('- 热度：GPTs × 0.3（近 24h × 0.5）\n- 走势：在涨', md)
        self.assertIn('- 热度：未测（Trends 没查到）\n- 走势：FindNews 发现时上升', md)
        self.assertIn('- 是做什么的：待查\n- 竞争：待查\n- 判断：待评', md)   # 留给 Agent 填


if __name__ == '__main__':
    unittest.main()
