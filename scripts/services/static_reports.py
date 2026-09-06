"""Compatibility report server with shared assets and same-origin read APIs."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'scripts' / 'services' / 'static'


class ReportHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        route = urlsplit(path).path
        if route.startswith('/assets/'):
            candidate = (ASSETS / route[len('/assets/'):]).resolve()
            if candidate.is_relative_to(ASSETS.resolve()) and candidate.is_file():
                return str(candidate)
            return str(ASSETS / '__not_found__')
        return super().translate_path(path)

    def do_GET(self):
        route = urlsplit(self.path).path
        if route in ('/dashboard.html', '/trading_trainer.html', '/watchlist.html',
                     '/symbol.html', '/minute_view.html', '/grid_simulator.html',
                     '/stock_journal.html', '/market_news.html'):
            # A report entry is only a compatibility link; interactive pages and
            # their write APIs must have the same origin on both desktop and LAN.
            host = self.headers.get('Host', '127.0.0.1').split(':')[0]
            if not host or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for c in host):
                self.send_error(400, 'Invalid host')
                return
            self.send_response(302)
            self.send_header('Location', 'http://' + host + ':8765' + self.path)
            self.end_headers()
            return
        if route == '/api/system/status':
            import json
            from scripts.services.system_status import public_status
            payload = json.dumps(public_status(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if route == '/api/market/context':
            try:
                with urlopen('http://127.0.0.1:8765' + self.path, timeout=5) as response:
                    payload = response.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(payload)
            except (OSError, URLError):
                self.send_error(503, 'Interactive service unavailable')
            return
        return super().do_GET()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    ThreadingHTTPServer(('127.0.0.1', args.port), partial(ReportHandler, directory=str(ROOT / 'output'))).serve_forever()


if __name__ == '__main__':
    main()
