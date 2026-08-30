"""时间序列研究的统一训练/验证切分。

所有接口都保持时间顺序，支持训练与验证之间的隔离期（embargo），避免标签窗口、
持仓窗口或数据延迟跨越边界造成泄漏。
"""

from dataclasses import asdict, dataclass
from typing import Any, List, Sequence, Tuple


@dataclass(frozen=True)
class TimeSplit:
    fold: int
    train: Tuple[Any, ...]
    validation: Tuple[Any, ...]
    embargo: Tuple[Any, ...] = ()

    def to_dict(self):
        return asdict(self)


def chronological_holdout(
    dates: Sequence[Any],
    validation_size: int,
    *,
    embargo_size: int = 0,
    min_train_size: int = 1,
) -> TimeSplit:
    """末段留作验证集，中间可留出隔离期。"""
    ordered = _validate_dates(dates)
    _validate_sizes(validation_size, embargo_size, min_train_size)
    train_end = len(ordered) - validation_size - embargo_size
    if train_end < min_train_size:
        raise ValueError("训练样本不足：请减少验证/隔离长度或增加总样本")
    embargo_end = train_end + embargo_size
    split = TimeSplit(
        fold=0,
        train=tuple(ordered[:train_end]),
        embargo=tuple(ordered[train_end:embargo_end]),
        validation=tuple(ordered[embargo_end:]),
    )
    assert_no_temporal_leakage(split)
    return split


def walk_forward_splits(
    dates: Sequence[Any],
    *,
    train_size: int,
    validation_size: int,
    step_size: int = None,
    embargo_size: int = 0,
    anchored: bool = False,
) -> List[TimeSplit]:
    """生成滚动或锚定扩张的 walk-forward 切分。"""
    ordered = _validate_dates(dates)
    _validate_sizes(validation_size, embargo_size, train_size)
    if train_size <= 0:
        raise ValueError("train_size 必须大于 0")
    step = validation_size if step_size is None else step_size
    if step <= 0:
        raise ValueError("step_size 必须大于 0")

    validation_start = train_size + embargo_size
    splits: List[TimeSplit] = []
    fold = 0
    while validation_start + validation_size <= len(ordered):
        train_end = validation_start - embargo_size
        train_start = 0 if anchored else train_end - train_size
        split = TimeSplit(
            fold=fold,
            train=tuple(ordered[train_start:train_end]),
            embargo=tuple(ordered[train_end:validation_start]),
            validation=tuple(
                ordered[validation_start:validation_start + validation_size]
            ),
        )
        assert_no_temporal_leakage(split)
        splits.append(split)
        validation_start += step
        fold += 1

    if not splits:
        raise ValueError("样本不足以生成一个 walk-forward 切分")
    return splits


def assert_no_temporal_leakage(split: TimeSplit) -> None:
    """检查集合互斥和严格时间先后。"""
    if not split.train or not split.validation:
        raise ValueError("训练集和验证集都不能为空")
    train, validation, embargo = set(split.train), set(split.validation), set(split.embargo)
    if train & validation or train & embargo or validation & embargo:
        raise ValueError("训练、隔离和验证集合不能重叠")
    if max(split.train) >= min(split.validation):
        raise ValueError("训练数据必须严格早于验证数据")
    if split.embargo:
        if max(split.train) >= min(split.embargo):
            raise ValueError("隔离期必须位于训练集之后")
        if max(split.embargo) >= min(split.validation):
            raise ValueError("隔离期必须位于验证集之前")


def _validate_dates(dates: Sequence[Any]) -> List[Any]:
    ordered = list(dates)
    if not ordered:
        raise ValueError("日期序列不能为空")
    if len(set(ordered)) != len(ordered):
        raise ValueError("日期序列不能包含重复值")
    if ordered != sorted(ordered):
        raise ValueError("日期序列必须按时间升序排列")
    return ordered


def _validate_sizes(validation_size: int, embargo_size: int,
                    min_train_size: int) -> None:
    if validation_size <= 0:
        raise ValueError("validation_size 必须大于 0")
    if embargo_size < 0:
        raise ValueError("embargo_size 不能为负数")
    if min_train_size <= 0:
        raise ValueError("min_train_size 必须大于 0")
