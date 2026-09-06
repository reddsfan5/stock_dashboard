import copy
import unittest
import tempfile
from pathlib import Path
from urllib.parse import urlencode, urlparse
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.services.training_context import context_state, journal_bars, journal_source, minute_payload, read_context


class TrainingContextTest(unittest.TestCase):
    def setUp(self):
        self.state = dict(code='sh600000', name='浦发银行', session_id='isolated',
            date='2026-09-03', time='09:31', prev_close=10,
            daily_bars=[dict(date='2026-09-02', open=9, high=10, low=9, close=10, amount=100),
                        dict(date='2026-09-03', open=10, high=10.1, low=10, close=10.1, amount=10, partial=True)],
            minute_points=[dict(time='09:31',open=10,high=10.1,low=10,close=10.1,volume=100,amount=1010,vwap=10.1)])
        self.params = dict(training_session=['isolated'],replay_date=['2026-09-03'],replay_time=['09:31'])
        self.handler = SimpleNamespace(trainer=Mock(state=Mock(return_value=self.state)),journal=Mock(),news=Mock())

    def test_expired_time_and_different_code_fail_closed(self):
        for changes in [dict(replay_time=['09:32']),dict(replay_date=['2026-09-04']),dict(code=['sh600519'])]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                context_state(self.handler.trainer,dict(self.params,**changes))

    def test_daily_projection_does_not_mutate_session_or_add_future_bars(self):
        before = copy.deepcopy(self.state)
        result = journal_bars(self.state)
        self.assertEqual(result['latest']['date'],'2026-09-03')
        self.assertTrue(result['latest']['partial'])
        self.assertEqual(result['latest']['high'],10.1)
        self.assertIsNone(result['latest']['ma5'])
        self.assertEqual(self.state,before)

    def test_minute_summary_uses_only_revealed_points(self):
        result = minute_payload(self.state)
        self.assertEqual(result['dates'],['2026-09-03'])
        self.assertEqual(len(result['points']),1)
        self.assertEqual(result['summary']['high'],10.1)
        self.assertEqual(result['summary']['amount'],1010)
        self.assertEqual(result['points'][-1]['time'],'09:31')

    def test_latest_context_and_unsupported_api_cannot_leak(self):
        self.handler.journal.list_cases.return_value=[]
        result = read_context(self.handler,'/api/symbol/context',self.params)
        self.assertEqual(result['screen_hits'],[])
        self.assertEqual(result['tracks']['items'],[])
        with self.assertRaises(ValueError):
            read_context(self.handler,'/api/watchlist/tracks',self.params)
        with self.assertRaises(ValueError):
            read_context(self.handler,'/api/minute/data',dict(self.params,date=['2026-09-04']))

    def test_journal_only_returns_this_training_sessions_notes(self):
        self.handler.journal.list_cases.return_value=[dict(id=1,source=journal_source(self.state)),dict(id=2,source='manual')]
        self.handler.journal.list_entries.return_value=[dict(id=1,case_id=1,market_date='2026-09-03'),
            dict(id=2,case_id=2,market_date='2026-09-02'),dict(id=3,case_id=1,market_date='2026-09-04')]
        rows=read_context(self.handler,'/api/journal/entries',self.params)
        self.assertEqual([r['id'] for r in rows],[1])

    def test_news_query_is_pinned_to_simulation_clock(self):
        read_context(self.handler,'/api/news/day',dict(self.params,date=['2099-01-01'],as_of=['15:00']))
        self.handler.news.day.assert_called_once_with('2026-09-03',as_of='09:31')

    def test_case_creation_is_compatible_with_existing_repository(self):
        from scripts.services.minute_viewer import MinuteRequestHandler
        from scripts.services.stock_journal import StockJournalService
        with tempfile.TemporaryDirectory() as temp:
            handler = object.__new__(MinuteRequestHandler)
            handler.trainer = self.handler.trainer
            handler.journal = StockJournalService({}, db_path=Path(temp)/'journal.sqlite3')
            handler._read_json = Mock(return_value=dict(code='sh600000',title='当前判断',tags='量价'))
            handler._send_json = Mock()
            query=urlencode({k:v[0] for k,v in self.params.items()})
            handler._journal_post(urlparse('/api/journal/case?'+query))
            created=handler._send_json.call_args.args[0]
            self.assertNotIn('error',created)
            self.assertEqual(created['source'],journal_source(self.state))
            self.assertEqual(created['tags'],['量价'])
            self.assertEqual(len(read_context(handler,'/api/journal/cases',self.params)),1)

    def test_expired_session_html_never_falls_back_to_static_payload(self):
        from scripts.services.minute_viewer import MinuteRequestHandler
        handler = object.__new__(MinuteRequestHandler)
        handler.trainer = self.handler.trainer
        handler._send_json = Mock()
        handler.path='/minute_view.html?'+urlencode(dict(training_session='isolated',replay_date='2026-09-04',replay_time='09:31'))
        handler.do_GET()
        self.assertEqual(handler._send_json.call_args.kwargs['status'],409)


if __name__ == '__main__':
    unittest.main()
