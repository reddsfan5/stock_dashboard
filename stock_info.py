"""
股票档案数据层 — 申万行业分类

数据源：申万宏源 legulegu（一级）+ swsresearch Excel（全三级，待修复）
缓存：stock_info.parquet

结构：
  代码     名称      申万1级   申万2级   申万3级
  sh600519 贵州茅台  食品饮料  白酒Ⅱ    白酒Ⅲ
"""

import os
import time
import pandas as pd
from typing import List, Optional
from tqdm import tqdm

import akshare as ak

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(PROJECT_DIR, "stock_info.parquet")

# 交易所前缀推断
def _add_prefix(raw: str) -> str:
    raw = str(int(raw)).zfill(6)
    return ("sh" + raw) if raw.startswith(("6", "9")) else ("sz" + raw)


class StockInfo:
    """股票档案：申万行业 + 名称"""

    def __init__(self):
        self._df: Optional[pd.DataFrame] = None

    @property
    def df(self) -> pd.DataFrame:
        if self._df is None:
            if os.path.exists(CACHE_FILE):
                self._df = pd.read_parquet(CACHE_FILE)
            else:
                self._df = pd.DataFrame(
                    columns=["代码", "名称", "申万1级", "申万2级", "申万3级"]
                )
        return self._df

    def _save(self):
        if self._df is not None:
            self._df.to_parquet(CACHE_FILE, index=False)

    # ========== 构建 ==========

    def build(self, force: bool = False):
        if not force and len(self.df) > 0:
            return self

        print("构建股票档案缓存（~3min）...")

        # ---- 申万一级行业（31 个，硬编码，legulegu 网站结构变动频繁） ----
        print("  申万一级行业成分股...")
        sw_level1 = pd.DataFrame([
            ("801010", "农林牧渔"), ("801020", "煤炭"), ("801030", "基础化工"),
            ("801040", "钢铁"), ("801050", "有色金属"), ("801080", "电子"),
            ("801110", "家用电器"), ("801120", "食品饮料"), ("801130", "纺织服饰"),
            ("801140", "轻工制造"), ("801150", "医药生物"), ("801160", "公用事业"),
            ("801170", "交通运输"), ("801180", "房地产"), ("801200", "商贸零售"),
            ("801210", "社会服务"), ("801230", "综合"), ("801710", "建筑材料"),
            ("801720", "建筑装饰"), ("801730", "电力设备"), ("801740", "国防军工"),
            ("801750", "计算机"), ("801760", "传媒"), ("801770", "通信"),
            ("801780", "银行"), ("801790", "非银金融"), ("801880", "汽车"),
            ("801890", "机械设备"), ("801950", "石油石化"), ("801960", "环保"),
            ("801970", "美容护理"),
        ], columns=["行业代码", "行业名称"])
        print(f"  {len(sw_level1)} 个一级行业")

        info_map = {}  # code → {name, sw1, sw2, sw3}

        for _, row in tqdm(sw_level1.iterrows(), total=len(sw_level1),
                           desc="  拉取成分股"):
            try:
                time.sleep(0.15)
                index_code = row["行业代码"]
                sw1_name = row["行业名称"]
                cons = ak.index_component_sw(symbol=index_code)
                for _, c in cons.iterrows():
                    code = _add_prefix(c["证券代码"])
                    if code not in info_map:
                        info_map[code] = {
                            "名称": str(c.get("证券名称", "")),
                            "申万1级": sw1_name,
                            "申万2级": "",
                            "申万3级": "",
                        }
                    elif not info_map[code]["申万1级"]:
                        info_map[code]["申万1级"] = sw1_name
            except Exception:
                pass

        # ---- 组装 ----
        try:
            spot = ak.stock_zh_a_spot_tx()[["code", "name"]]
            spot.columns = ["代码", "名称"]
        except Exception:
            spot = pd.DataFrame(columns=["代码", "名称"])

        records = []
        for _, s in spot.iterrows():
            code = s["代码"]
            info = info_map.get(code, {})
            records.append({
                "代码": code,
                "名称": info.get("名称") or s["名称"],
                "申万1级": info.get("申万1级", ""),
                "申万2级": info.get("申万2级", ""),
                "申万3级": info.get("申万3级", ""),
            })

        self._df = pd.DataFrame(records)
        self._save()

        mapped = sum(1 for r in records if r["申万1级"])
        print(f"✓ 构建完成: {len(self._df)} 只, {mapped} 只含申万一级行业")

        # 行业分布
        dist = self._df[self._df["申万1级"] != ""]["申万1级"].value_counts()
        print(f"  覆盖 {len(dist)} 个行业, 前5: {dict(dist.head(5))}")
        return self

    # ========== 查询 ==========

    def get(self, code: str) -> dict:
        row = self.df[self.df["代码"] == code]
        if len(row) == 0:
            return {"申万1级": "", "申万2级": "", "申万3级": "", "名称": ""}
        return row.iloc[0].to_dict()

    def get_name(self, code: str) -> str:
        row = self.df[self.df["代码"] == code]
        return row.iloc[0]["名称"] if len(row) > 0 else ""

    def all_sectors(self, level: int = 1) -> List[str]:
        col = f"申万{level}级"
        return sorted(self.df[col].dropna().replace("", pd.NA).dropna().unique())

    def stocks_in_sector(self, sector: str, level: int = 1) -> pd.DataFrame:
        col = f"申万{level}级"
        return self.df[self.df[col] == sector]

    def merge(self, df: pd.DataFrame, on: str = "代码") -> pd.DataFrame:
        return df.merge(
            self.df[["代码", "名称", "申万1级", "申万2级", "申万3级"]],
            on=on, how="left", suffixes=("", "_info"),
        )

    def update(self):
        if len(self.df) == 0:
            self.build()
        return self


if __name__ == "__main__":
    info = StockInfo()
    info.build()
