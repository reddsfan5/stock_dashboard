"""Browser QA server: production market cache, isolated mutable stores.

Run with python -m scripts.tools.ui_fixture_server. Never touches state/*.sqlite3.
"""
import tempfile
from functools import partial
from pathlib import Path
from http.server import ThreadingHTTPServer

from data.hypotheses import HypothesisRepository
from data.journal import JournalRepository
from data.training_sessions import TrainingSessionRepository
from data.watchlist import WatchlistRepository
from scripts.services.minute_viewer import MinuteRepository, MinuteRequestHandler, PROJECT_DIR
from scripts.services.trading_trainer import TradingTrainerService
from scripts.services.stock_journal import StockJournalService
from scripts.services.watchlist import WatchlistService
from scripts.services.symbol_context import SymbolContextService


class EmptyNews:
    def day(self, date, **kwargs):
        return dict(market_date=date, items=[], count=0, complete=True, message='测试资讯', as_of=kwargs.get('as_of'))

    def impacts(self, **kwargs):
        return dict(items=[], count=0)


class EmptyContext:
    def context(self, date, **kwargs):
        return dict(market_date=date, as_of=kwargs.get('as_of'), a_share=[], overseas=[], message='隔离测试')


def main():
    with tempfile.TemporaryDirectory(prefix='stock-ui-qa-') as temp:
        root = Path(temp)
        repo = MinuteRepository(build_search_index=True)
        training = TrainingSessionRepository(root / 'training.sqlite3')
        journal = JournalRepository(root / 'journal.sqlite3')
        watchlist = WatchlistRepository(root / 'watchlist.sqlite3')
        hypotheses = HypothesisRepository(root / 'hypotheses.sqlite3')
        class Handler(MinuteRequestHandler):
            pass
        Handler.repository = repo
        Handler.trainer = TradingTrainerService(repo, training_store=training)
        Handler.journal = StockJournalService(repo.name_map, db_path=root / 'journal.sqlite3')
        Handler.watchlist = WatchlistService(repository=watchlist, name_map=repo.name_map)
        Handler.symbol_context = SymbolContextService(name_map=repo.name_map, training=training,
            journal=journal, watchlist=watchlist, hypotheses=hypotheses)
        Handler.news = EmptyNews()
        Handler.market_context = EmptyContext()
        server = ThreadingHTTPServer(('127.0.0.1', 8877), partial(Handler, directory=str(Path(PROJECT_DIR) / 'output')))
        print('Isolated QA: http://127.0.0.1:8877 (temporary databases)', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
