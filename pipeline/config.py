"""管线配置管理 — 加载 YAML 并注入模块参数"""

import os
from typing import Dict, List
import yaml

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(path: str = None) -> dict:
    """加载 YAML 配置文件"""
    if path is None:
        path = os.path.join(PROJECT_DIR, "config", "pipeline.yaml")
    if os.path.exists(path):
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def apply_config(config: dict, modules: List) -> List:
    """把配置文件中的参数注入模块 kwargs"""
    for m in modules:
        if m.id in config:
            m.kwargs = {**m.kwargs, **config[m.id]}
    return modules
