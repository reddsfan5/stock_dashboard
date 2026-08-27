# Demo 包 — 量化库实践示例

对应 [docs/10-库与生态.md](../docs/10-库与生态.md) 的落地清单，把调研的库结合本项目真实数据（
`cache/*.parquet`）跑通最小示例。每个 demo 自包含、可离线、产出到 `output/`。

## 环境说明（两个 Python）

| 环境 | Python | 用途 |
|------|--------|------|
| 系统 | `/usr/local/bin/python`（launchd 同款） | 大部分 demo + 生产链路 |
| venv | `.venv/bin/python` | 仅 vectorbt（py3.9 下 vectorbt 锁 numba<0.57→numpy<1.24，与系统 numpy 2.0 冲突，隔离运行） |

## 安装

```bash
# 1. 系统 python 的 demo 依赖
brew update                                         # 旧版 brew 不认 macOS 26，先更新
brew install ta-lib                                 # TA-Lib 的 C 库（talib-binary 已从 PyPI 下架）
/usr/local/bin/python -m pip install -r demos/requirements.txt

# 2. vectorbt 专属 venv（已建好则跳过；重建命令）
/usr/local/bin/python -m venv --system-site-packages .venv
.venv/bin/pip install -r demos/requirements-venv.txt   # numpy1.23.5+numba0.56.4+vectorbt0.26+plotly5.13

# 3. 生产新增依赖（已在根 requirements.txt）
/usr/local/bin/python -m pip install baostock
```

## 运行顺序

| # | 命令 | 产出 | 说明 |
|---|------|------|------|
| 1 | `/usr/local/bin/python demos/demo_sources.py` | 终端对比表 | 三数据源同标的同时段对比，验证双源一致性 |
| 2 | `/usr/local/bin/python demos/demo_quantstats.py` | `output/quantstats_demo.html` | 策略 8 的 Sharpe/回撤/收益曲线（自动跑策略 8 出 CSV） |
| 3 | `/usr/local/bin/python demos/demo_talib.py` | 终端对比 | TA-Lib vs 自写指标互验 + RSI/ATR/MACD 展示 |
| 4 | `.venv/bin/python demos/demo_vectorbt.py` | `output/sweep_heatmap_demo.png` | 30 只 ETF 的 3×3 参数扫描热力图（首次编译 10-30s） |
| 5 | `/usr/local/bin/python demos/demo_pyecharts.py` | `output/demo_kline.html` | 510050 交互 K 线（自包含，可离线打开） |

生产工具（demo 的放大版）：
- `scripts/quant_report.py` — 任意权益/交易 CSV 的绩效报告
- `scripts/sweep.py` — 全市场 ETF/股票参数扫描（venv python 运行）

## 注意事项

- **baostock 成交额单位**：返回「元」，`data/sources.py` 已按缓存约定（万元）÷1e4；demo_sources 实测验证过比率
- **周末可跑**：所有数据来自本地缓存，不依赖交易日；网络接口只用于 demo_sources 的实时对比
- **efinance 偶发失败**：东财 push2his 接口连接重置（2026-08-16 实测），demo 会重试一次后跳过——这正是双数据源存在的意义
- **py3.9 版本锁**：vectorbt 0.26 在 py<3.10 要求 numba<0.57（numpy<1.24）；numba 0.59 与 numpy 2.0 的组合在 py3.9 上不兼容，勿升级
