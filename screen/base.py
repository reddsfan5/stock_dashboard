"""
选股模块基类

所有 screen 模块遵循统一接口：
  - PIPELINE_META: 模块元信息（id, title, kwargs）
  - find_all(data, **kwargs) → DataFrame: 核心分析方法
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import pandas as pd


@dataclass
class PipelineMeta:
    """管线模块元信息"""
    id: str
    title: str
    kwargs: dict = field(default_factory=dict)
    variants: List[dict] = field(default_factory=list)


class BaseScreener(ABC):
    """选股模块抽象基类"""

    meta: PipelineMeta

    @abstractmethod
    def find_all(self, data, **kwargs) -> pd.DataFrame:
        """核心分析方法：从 StockData 中找出满足条件的股票"""
        ...

    @classmethod
    def get_meta(cls) -> PipelineMeta:
        """获取模块元信息（默认从类属性读取）"""
        return cls.meta
