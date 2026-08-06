"""
管线执行引擎 — 模块发现 + 并行执行

设计：
  - 自动扫描 screen/ 目录发现选了股模块（PIPELINE_META + find_all）
  - 并行执行所有模块，统一关联申万行业信息
  - 配置注入：从 pipeline_config.yaml 覆盖模块参数
"""

import importlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class PipelineModule:
    """管线模块定义"""
    id: str
    title: str
    module: str                           # Python 模块名 (e.g. "screen.continuity")
    kwargs: dict = field(default_factory=dict)
    csv: str = ""

    def __post_init__(self):
        if not self.csv:
            self.csv = f"{self.id}.csv"


def discover_modules(screen_dir: str = None) -> List[PipelineModule]:
    """
    扫描 screen/ 目录，自动发现实现了 PIPELINE_META + find_all 的模块
    """
    import glob
    if screen_dir is None:
        screen_dir = os.path.join(PROJECT_DIR, "screen")

    modules = []
    for f in sorted(glob.glob(os.path.join(screen_dir, "*.py"))):
        name = os.path.splitext(os.path.basename(f))[0]
        if name in ("__init__", "base", "engine"):
            continue
        try:
            m = importlib.import_module(f"screen.{name}")
            meta = getattr(m, "PIPELINE_META", None)
            fn = getattr(m, "find_all", None)
            if not meta or not fn:
                continue

            # 支持 variants：一个模块产出多组结果
            variants = meta.get("variants", [None])
            for v in variants:
                if v:
                    kw = {**meta.get("kwargs", {}), **v.get("kwargs", {})}
                    modules.append(PipelineModule(
                        id=v["id"], title=v["title"],
                        module=f"screen.{name}", kwargs=kw,
                    ))
                else:
                    modules.append(PipelineModule(
                        id=meta.get("id", name),
                        title=meta.get("title", name),
                        module=f"screen.{name}",
                        kwargs=meta.get("kwargs", {}),
                    ))
        except Exception as e:
            print(f"  ⚠ 加载 screen.{name} 失败: {e}")

    return modules


def run_module(mod: PipelineModule, data, tqdm_position: int = 0) -> pd.DataFrame:
    """动态加载模块并调用 find_all()"""
    m = importlib.import_module(mod.module)
    fn = getattr(m, "find_all", None)
    if fn is None:
        print(f"  ⚠ {mod.title}: 缺少 find_all() 接口")
        return pd.DataFrame()
    tqdm_kwargs = {"position": tqdm_position, "leave": False}
    return fn(data, **mod.kwargs, _tqdm_kwargs=tqdm_kwargs)


def run_all(data, modules: List[PipelineModule],
            only: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
    """并行执行所有管线模块，关联申万行业信息"""
    if only:
        modules = [m for m in modules if m.id in only]

    results = {}
    start = time.time()
    print(f"\n{'='*50}")
    print(f"执行 {len(modules)} 个分析模块（并行）...")
    print(f"{'='*50}")

    # 给每个模块分配独立的 tqdm 行号，避免并发时进度条互相覆盖
    with ThreadPoolExecutor(max_workers=len(modules)) as executor:
        futures = {
            executor.submit(run_module, m, data, tqdm_position=idx): m
            for idx, m in enumerate(modules)
        }
        for future in as_completed(futures):
            m = futures[future]
            try:
                df = future.result()
                if len(df) > 0:
                    from data.industry import StockInfo
                    info = StockInfo()
                    info_cols = info.df[["代码", "名称", "申万1级", "申万2级", "申万3级"]]
                    for col in ["名称", "申万1级", "申万2级", "申万3级"]:
                        if col in df.columns:
                            df = df.drop(columns=[col])
                    df = df.merge(info_cols, on="代码", how="left")
                    # ETF 等非股票标的回填名称和板块
                    null_names = df["名称"].isna()
                    if null_names.any():
                        df.loc[null_names, "名称"] = df.loc[null_names, "代码"].apply(
                            lambda c: data.get_stock_name(c) if hasattr(data, "get_stock_name") else ""
                        )
                        df.loc[null_names, "申万1级"] = "ETF"
                results[m.id] = df
                print(f"  ✓ {m.title}: {len(df)} 只")
            except Exception as e:
                print(f"  ✗ {m.title}: {e}")
                results[m.id] = pd.DataFrame()

    elapsed = time.time() - start
    print(f"\n总耗时: {elapsed:.1f} 秒")
    return results
