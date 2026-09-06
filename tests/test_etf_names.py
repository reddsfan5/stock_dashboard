import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from data.etf_names import load_names, save_names
from scripts.services.minute_viewer import MinuteRepository
from scripts.tools.refresh_etf_names import fetch_names


class ETFNamesTests(unittest.TestCase):
    def test_offline_cache_merges_and_rejects_invalid_shape(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'names.json'
            self.assertEqual(load_names(path), {})
            save_names({'sh510500': '测试500ETF'}, path)
            save_names({'sz159919': '测试300ETF'}, path)
            self.assertEqual(len(load_names(path)), 2)
            path.write_text('[]')
            self.assertEqual(load_names(path), {})

    def test_repository_loads_etfs_even_without_stock_archive(self):
        with patch('data.industry.StockInfo', side_effect=OSError), patch(
            'data.etf_names.load_names', return_value={'sh510500': '测试500ETF'}
        ):
            self.assertEqual(MinuteRepository._load_names()['sh510500'], '测试500ETF')

    def test_stock_names_preserved(self):
        with patch('data.industry.StockInfo') as info, patch(
            'data.etf_names.load_names', return_value={'sh510500': '测试500ETF'}
        ):
            info.return_value.df = pd.DataFrame({'代码':['sh600000'], '名称':['浦发银行']})
            self.assertEqual(MinuteRepository._load_names(), {'sh600000':'浦发银行','sh510500':'测试500ETF'})

    def test_vendor_response_validates_code_and_empty_results(self):
        with patch('scripts.tools.refresh_etf_names.requests.get') as get:
            get.return_value.text = 'v_sh510500="1~测试500ETF~510500~6.2";v_sz159919="1~错误匹配~000001";'
            self.assertEqual(fetch_names(['sh510500','sz159919']), {'sh510500':'测试500ETF'})


if __name__ == '__main__':
    unittest.main()
