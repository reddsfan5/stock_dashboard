"""Refresh names for locally cached ETFs; never modifies market prices."""
import re
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import requests
from data.etf import CACHE_FILE
from data.etf_names import save_names


def fetch_names(codes):
    response = requests.get('https://qt.gtimg.cn/q=' + ','.join(codes), timeout=15)
    response.raise_for_status()
    response.encoding = 'gbk'
    names = {}
    for code, value in re.findall(r'v_((?:sh|sz)\d{6})="([^"]*)"', response.text):
        fields = value.split('~')
        if len(fields) > 2 and fields[2] == code[2:] and fields[1].strip():
            names[code] = fields[1].strip()
    return names


def main():
    raw = pd.read_parquet(CACHE_FILE, columns=['代码'])['代码'].unique()
    codes = [('sh' if str(code).startswith('5') else 'sz') + str(code).zfill(6) for code in raw]
    names = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(fetch_names, [codes[i:i+50] for i in range(0, len(codes), 50)]):
            names.update(result)
    if not names:
        raise RuntimeError('No ETF names returned; existing metadata preserved')
    save_names(names)
    print(f'ETF names: {len(names)}/{len(codes)}')
    missing = sorted(set(codes) - names.keys())
    if missing:
        print('Unavailable names:', ', '.join(missing))


if __name__ == '__main__':
    main()
