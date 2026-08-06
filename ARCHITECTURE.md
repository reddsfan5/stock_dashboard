# 项目架构文档

## 目录结构

```
stock/
├── config/                       # 配置文件
│   └── pipeline.yaml             # 选股参数（阈值、天数等）
│
├── cache/                        # 本地数据缓存
│   ├── stock_kline_cache.parquet # K线（4396只，1172万行，162MB）
│   ├── stock_info.parquet        # 申万2021行业分类（5534只）
│   └── stock_info.xlsx           # 同上，Excel 格式
│
├── data/                         # 数据层
│   ├── kline.py                  # StockData — 缓存管理、增量更新、查询API
│   └── industry.py               # StockInfo — 申万行业分类（389个行业码→名称映射）
│
├── screen/                       # 选股分析层
│   ├── base.py                   # BaseScreener 抽象基类 + PipelineMeta
│   ├── engine.py                 # Screener 通用排序/过滤/导出引擎
│   ├── continuity.py             # K线连续性选股（接续不中断）
│   ├── sideways.py               # 横盘震荡检测（100分综合评分）
│   ├── trend.py                  # 连续涨/跌趋势选股
│   └── hammer.py                 # 金针探底形态识别
│
├── backtest/                     # 回测统计层
│   ├── engine.py                 # StatsEngine + 12种策略（模板方法模式）
│   └── grid.py                   # 网格交易回测（策略模式，两种网格）
│
├── pipeline/                     # 管线编排层
│   ├── runner.py                 # 模块自动发现 + 并行执行
│   ├── reporter.py               # 统一 HTML 报告生成
│   └── config.py                 # YAML 配置加载 + 参数注入
│
├── scripts/                      # 入口脚本
│   ├── screen.py                 # 选股管线 → dashboard.html
│   ├── backtest.py               # 回测管线 → stats_report.html
│   ├── debug_overlap.py          # 单股票逐笔调试（Overlap策略）
│   ├── sim_overlap.py            # 单股票资金模拟
│   └── sim_portfolio.py          # 全市场组合模拟
│
├── output/                       # 生成输出（HTML、CSV等）
├── stock_data.py                 # → data.kline（向后兼容 shim）
├── stock_info.py                 # → data.industry（shim）
└── update_cache.py               # 定时缓存更新（launchd 每日18:30）
```

---

## 一、数据准备

### 1.1 K线缓存更新

```bash
# 增量更新（拉取缺失股票的近期数据，秒级~分钟级）
python update_cache.py

# 或代码调用
from data.kline import StockData
data = StockData()
data.update()                     # 增量
data.rebuild(start_date="20100101")  # 全量重建
```

缓存范围：2010-01-01 ~ 至今，排除北交所（bj）和科创板（sh688），约 4400 只。

### 1.2 行业分类更新

```bash
python -c "from data.industry import StockInfo; StockInfo().build(force=True)"
```

数据源：申万宏源 `StockClassifyUse_stock.xls` + 内置 SW2021 代码→名称对照表（389 个三级行业）。

---

## 二、选股管线

### 2.1 运行

```bash
# 全部 5 个模块
python scripts/screen.py

# 指定模块（逗号分隔）
python scripts/screen.py --only continuity,sideways

# 先更新缓存再跑
python scripts/screen.py --refresh

# 输出到指定路径
python scripts/screen.py --out output/my_dashboard.html
```

### 2.2 模块列表

| 模块 ID | 标题 | 说明 |
|---------|------|------|
| `continuity` | K线连续性 | 每天最高价持续高于前日最低价一定比例，K线接续不中断 |
| `sideways` | 横盘震荡 | 振幅小、趋势平坦、K线重叠率高（100分综合评分） |
| `trend-up` | 连续上涨(5日) | 连续 N 天收阳，过滤涨停连板妖股 |
| `trend-down` | 连续下跌(5日) | 连续 N 天收阴，寻找超跌反弹机会 |
| `hammer` | 金针探底 | 近 3 日出现长下影线探底形态 |

### 2.3 参数配置

编辑 [`config/pipeline.yaml`](config/pipeline.yaml)：

```yaml
continuity:
  min_gap_pct: 3         # 最小接续区%（越大越严）
  strict: true            # true=K线必须重叠不允许跳空
  min_amount: 5000       # 日均成交额下限（万元）
  lookback: 12            # 回看天数

sideways:
  days: 10                # 横盘回看天数
  max_amp: 15             # 最大振幅%
  max_slope: 0.5          # 趋势斜率上限
  min_overlap: 30         # 最小平均重叠率%
  r2_max: 0.3             # 线性R²上限

hammer:
  min_shadow_ratio: 3     # 下影线/实体最小倍数
  hammer_days: 3          # 近N天内出现即可
  max_bottom_pos: 8       # 金针最低价在区间底部N%以内
```

### 2.4 输出

生成 `output/dashboard.html`（或 `--out` 指定路径）：
- 桌面版：DataTables 可排序表格 + ECharts K线弹窗（点击股票代码查看60日K线）
- 支持：搜索、排序、分页、申万行业筛选

---

## 三、回测统计

### 3.1 运行

```bash
# 全部 12 个策略
python scripts/backtest.py

# 指定策略
python scripts/backtest.py --only overlap,rising,baseline

# 指定日期范围（只统计 N 年至今）
python scripts/backtest.py --start 2024-01-01 --only overlap,rising

# 输出到指定路径
python scripts/backtest.py --out output/my_report.html
```

### 3.2 策略列表

| ID | 说明 | 类型 |
|----|------|------|
| `baseline` | 基准：任意天低买次日高卖 | 单次 |
| `overlap` | 重叠>3%低买高卖 | 多N值(5-10) |
| `overlap_close` | 重叠>3%收盘买入高卖 | 多N值 |
| `rising` | 连续上涨后次日涨跌 | 多N值 |
| `newhigh` | 连涨后5日创新高 | 单次 |
| `volume_up` | 放量上涨后次日 | 多N值 |
| `volume_down` | 缩量上涨后次日 | 多N值 |
| `volume_dry` | 地量后突破 | 多N值 |
| `gap_fill` | 跳空缺口回补(10日) | 单次 |
| `oversold` | 连续下跌后反弹 | 多N值 |
| `bollinger` | 布林带收敛突破 | 多N值 |
| `multi_signal` | 双信号：重叠+缩量 | 多N值 |

### 3.3 输出

生成 Chart.js 交互图表报告：
- 策略对比散点气泡图（X=样本量, Y=成功率, 气泡=收益）
- 成功率排名柱状图
- 每个策略独立的：成功率柱状图、收益vs样本量组合图、目标收益热力图
- 详细数据表格

---

## 四、单股票调试

### 4.1 Overlap 策略逐笔调试

```bash
# 比亚迪，连续3天重叠>2%，理想模式
python scripts/debug_overlap.py --code sz002594 --days 3 --pct 2.0

# 茅台，连续5天重叠>3%，真实挂单模式
python scripts/debug_overlap.py --code sh600519 --days 5 --pct 3.0 --realistic --target 1.0
```

终端输出每笔信号的详细信息：
- 连续重叠天数（含追溯实际重叠长度）
- 每日重叠区百分比
- 买入日/卖出日/买入价/卖出价
- 收益（挂单成交 or 尾盘平仓）

同时生成 `output/overlap_debug.html`：
- 收益分布柱状图、逐年胜率图
- 全部信号明细表（含重叠天数、买卖日期、价格、盈亏）

### 4.2 单股票资金模拟

```bash
# 比亚迪，5万起步，2020年至今，挂单+1%
python scripts/sim_overlap.py --code sz002594

# 茅台，100万起步
python scripts/sim_overlap.py --code sh600519 --capital 1000000 --days 5 --pct 3.0

# 2024年起
python scripts/sim_overlap.py --code sz002594 --start 2024-01-01
```

模拟规则：
- 起始资金全部可用
- 每笔信号买最大手数（100股/手）× 尽可能多
- 挂限价单 +1%，触及成交，否则尾盘收盘价卖出
- 含佣金（万分之一）+ 印花税（万分之五）

生成 `output/overlap_sim.html`：权益曲线 + 每笔收益图 + 交易明细表。

---

## 五、全市场组合模拟

### 5.1 运行

```bash
# 默认：5万起步，2024年至今，全市场主板
python scripts/sim_portfolio.py

# 100万起步
python scripts/sim_portfolio.py --capital 1000000

# 调整重叠阈值
python scripts/sim_portfolio.py --overlap 2.0          # 重叠>2%（更宽松）

# 调整佣金（万分之N）
python scripts/sim_portfolio.py --commission 2.5         # 万2.5

# 调整挂单目标
python scripts/sim_portfolio.py --target 2.0             # 挂单+2%
```

### 5.2 策略逻辑

1. **选股**：每日扫描全市场主板股票，找连续(3-10)天K线重叠区 > 3% 的标的
2. **分配**：随机选 2 只，各买最大手数；剩余现金按价格从低到高，买到不够买最便宜的一手
3. **卖出**：次日挂限价单 +1%，触及→按目标价成交；未触及→尾盘收盘价卖出
4. **复利**：每日回收现金后全仓再投入

### 5.3 费用

- 买入佣金：万分之一（可调 `--commission`）
- 卖出佣金：万分之一 + 印花税万分之五
- 每手 100 股，只买整手

### 5.4 输出

生成 `output/portfolio_sim.html`：
- 权益曲线（Chart.js 折线图）
- 每日持仓数（柱状图）
- 按月分组的交易明细（可展开/折叠，含股票代码+名称+手数+买卖价格+盈亏）
- 月度统计：交易数、胜率、总盈亏

---

## 六、添加新的选股模块

1. 在 `screen/` 下创建新文件（如 `screen/breakout.py`）
2. 定义 `PIPELINE_META` 和 `find_all(data, **kwargs)` 函数：

```python
# screen/breakout.py
PIPELINE_META = {"id": "breakout", "title": "突破形态", "kwargs": {"days": 10}}

def find_all(data: StockData, **kwargs) -> pd.DataFrame:
    days = kwargs.get("days", 10)
    # ... 筛选逻辑 ...
    return result_df
```

3. 在 `config/pipeline.yaml` 中添加对应参数（可选）：

```yaml
breakout:
  days: 10
  min_volume: 10000
```

4. 运行即自动发现：

```bash
python scripts/screen.py --only breakout
```

---

## 七、数据流

```
cache/stock_kline_cache.parquet
    │
    ▼
data/kline.py (StockData)
    │
    ├──→ screen/*.py (find_all) ──→ pipeline/runner.py (并行执行)
    │                                      │
    │                                      ▼
    │                               pipeline/reporter.py (build_screening_html)
    │                                      │
    │                                      ▼
    │                               output/dashboard.html
    │
    ├──→ backtest/engine.py (StatsEngine) ──→ scripts/backtest.py
    │                                              │
    │                                              ▼
    │                                       pipeline/reporter.py (build_backtest_html)
    │                                              │
    │                                              ▼
    │                                       output/stats_report.html
    │
    └──→ scripts/sim_portfolio.py (模拟引擎) ──→ output/portfolio_sim.html
```

---

## 八、设计模式

| 模式 | 位置 | 说明 |
|------|------|------|
| 模板方法 | `backtest/engine.py` | `WindowStats.run()` 固定流程，子类实现条件/结果计算 |
| 策略模式 | `backtest/grid.py` | `AbstractGridStrategy` 可互换的网格交易策略 |
| 管道模式 | `screen/engine.py` | `Screener` 链式调用 `.join_info().filter().sort().head()` |
| 自注册 | `screen/base.py` | `PIPELINE_META` + `pipeline/runner.py` 自动发现 |
| Builder | `backtest/grid.py` | `GridConfig` 链式构建网格参数 |
