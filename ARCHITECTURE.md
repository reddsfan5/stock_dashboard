# 项目架构文档

## 目录结构

```
stock/
├── config/                       # 配置（pipeline.yaml、openai*.yaml）
│
├── cache/                        # 本地 Parquet / 运行时状态（不提交大体量行情）
│   ├── stock_kline_cache.parquet # 股票日 K
│   ├── etf_kline_cache.parquet   # ETF 日 K
│   ├── index_kline_cache.parquet # 指数日 K
│   ├── index_minute_cache.parquet# A股指数分钟（训练情境）
│   ├── global_markets_cache.parquet # 海外指数日线
│   ├── trading_calendar.parquet  # A股交易日历（半日市标记）
│   ├── daily_limit_facts.parquet # 日度涨跌停/停牌事实
│   ├── stock_status.parquet      # ST/名称状态
│   ├── minute_kline_cache.parquet# 1 分钟 K（股票+ETF）
│   ├── daily_basic_cache.parquet # 估值/市值/供应商量比快照
│   ├── stock_info.parquet        # 申万 2021 行业 + 名称
│   ├── indicators_*.parquet      # 兼容旧日频指标透视表
│   ├── features/                 # 版本化特征缓存（core/liquidity/research）
│   ├── services/                 # serve 进程记录（web.json 等）
│   ├── recovery/                 # 损坏/中断时的恢复副本
│   └── daily_update_status.json  # 每日更新阶段状态
│
├── state/                        # 个人 SQLite（不可再生研究数据，不提交）
│   ├── stock_journal.sqlite3     # 选股日记案例与事件
│   ├── market_news.sqlite3       # 市场资讯缓存与影响记录
│   ├── training_sessions.sqlite3 # T+1 训练闭环（计划/决策/心态）
│   ├── watchlist.sqlite3         # 观察池 / 待买池与次日跟踪
│   └── backups/                  # 日记一致性备份
│
├── data/                         # 数据层（事实读写，不含特征公式）
│   ├── schema.py                 # 日 K / 日度基础字段契约与单位
│   ├── sources.py                # 外部源适配：腾讯/AkShare 主源 + BaoStock 备源等
│   ├── storage.py                # Parquet/JSON 原子写入
│   ├── kline.py                  # StockData — 股票日 K 缓存与查询
│   ├── etf.py                    # ETFData — ETF 日 K
│   ├── index.py                  # IndexData — 大盘指数日 K
│   ├── minute.py                 # MinuteData — 1 分钟 K
│   ├── daily_basic.py            # DailyBasicData — 估值/市值快照
│   ├── enrichment.py             # 用分钟量补齐日 K 空成交量
│   ├── industry.py               # StockInfo — 申万行业分类
│   ├── journal.py                # 选股日记 SQLite
│   ├── market_news.py            # 市场资讯 SQLite + 同花顺公开源
│   ├── index_minute.py           # A股宽基指数分钟缓存
│   ├── global_markets.py         # 海外指数日线（港/美/韩）
│   └── market_context.py         # 训练页市场情境 as_of 组装
│
├── features/                     # 特征层（可复算指标，与事实分离）
│   ├── catalog.py                # 特征目录与 profile（core/liquidity/research）
│   ├── daily.py                  # 日频特征计算
│   ├── intraday.py               # 盘中量比等
│   ├── store.py                  # 特征缓存读写与失效
│   ├── snapshot.py               # 选股网页决策快照
│   └── revealed.py               # T+1 训练防剧透指标
│
├── screen/                       # 选股分析层（PIPELINE_META 自注册）
│   ├── base.py / engine.py       # 基类与链式 Screener
│   ├── continuity.py             # K线连续性
│   ├── sideways.py               # 横盘震荡
│   ├── trend.py                  # 连续涨/跌（variants: trend-up / trend-down）
│   ├── hammer.py                 # 金针探底
│   ├── long_shadow.py            # 长下影线
│   └── upward_gap.py             # 持续推高
│
├── backtest/                     # 回测 / 模拟 / 训练核心库
│   ├── engine.py                 # StatsEngine + 模板策略（scripts.backtest 注册 12 个）
│   ├── strategy.py / indicators.py
│   ├── grid.py / intraday_grid.py# 日频与分钟网格
│   ├── sim_engine.py / sim_core.py / sim_types.py
│   ├── execution.py / rebalance.py / validation.py / metrics.py
│   ├── trading_trainer.py        # T+1 手动交易状态机
│   ├── proverbs.py / proverb_report.py
│   ├── slow_rise.py / slow_rise_report.py
│   ├── screen_to_trade.py / screen_to_trade_report.py  # 选股→交易链接回测
│   └── renderer.py / templates/
│
├── pipeline/                     # 管线编排
│   ├── runner.py                 # 选股模块发现 + 并行执行
│   ├── reporter.py               # HTML 报告
│   ├── daily_update.py           # 每日分阶段更新 + 质量门禁
│   └── config.py                 # YAML 加载与参数注入
│
├── scripts/                      # 命令入口（顶层仅全局编排）
│   ├── update_cache.py           # → pipeline.daily_update
│   ├── screen.py / backtest.py / run_all_strategies.py
│   ├── serve.py                  # 两个 HTTP 进程的统一启停与健康检查
│   ├── strategies/               # 独立研究页 strategy_01 ~ strategy_14（与 engine 模板策略分离）
│   ├── simulations/              # 资金与交易流程模拟
│   ├── research/                 # 专题回测与统计
│   ├── reports/                  # 导航与市场页生成
│   ├── services/                 # 交互 Web 业务实现（共用 8765）
│   └── tools/                    # 调试与参数扫描
│
├── tests/                        # pytest（合约、服务、日记、训练等）
├── demos/                        # 第三方库演示（非业务入口）
├── output/                       # 生成的 HTML / CSV / logs
└── docs/                         # 文档
```

说明：根目录偶发的 `xlsx`、`mac_recommend/`、截图等**不是**正式架构的一部分。

脚本职责与归类规则见 [`scripts/README.md`](scripts/README.md)。

---

## 一、数据准备

### 1.1 K线缓存更新

```bash
# 增量更新（拉取缺失股票的近期数据，秒级~分钟级）
python -m scripts.update_cache

# 只检查股票/ETF/指数/分钟缓存是否覆盖同一目标交易日
python -m scripts.update_cache --validate-only

# 或代码调用
from data.kline import StockData
data = StockData()
data.update()                     # 增量
data.rebuild(start_date="20100101")  # 全量重建
```

日常任务由 `pipeline.daily_update` 按阶段执行：
`stocks → etfs → index → minute → enrich → validate → news → market_context → stock_facts → watchlist_track → reports`。
状态写入 `cache/daily_update_status.json`；关键数据未达到覆盖率门禁时命令返回非零退出码。
分钟线每 1000 只原子落盘，可在中断后续跑。

缓存范围：2010-01-01 ~ 至今，排除北交所（bj）和科创板（sh688）。
股票/ETF/指数/分钟/日度基础快照分文件存放于 `cache/`；选股日记、市场资讯与训练闭环落在 `state/*.sqlite3`。交易日历与涨跌停事实亦在 `cache/`。
外部行情主源为腾讯（AkShare + qt.gtimg.cn 快照），BaoStock 为失败子集备源；
首次全量重建与分钟备源可用新浪。行业分类来自申万 2021。

### 1.2 行业分类更新

```bash
python -c "from data.industry import StockInfo; StockInfo().build(force=True)"
```

数据源：申万宏源 `StockClassifyUse_stock.xls` + 内置 SW2021 代码→名称对照表（389 个三级行业）。

---

## 二、选股管线

### 2.1 运行

```bash
# 全部选股模块
python -m scripts.screen

# 指定模块（逗号分隔）
python -m scripts.screen --only continuity,sideways

# 先更新缓存再跑
python -m scripts.screen --refresh

# 输出到指定路径
python -m scripts.screen --out output/my_dashboard.html
```

### 2.2 模块列表

管线自动发现 `screen/` 下带 `PIPELINE_META` 的模块（含 `variants`）：

| 模块 ID | 标题 | 说明 |
|---------|------|------|
| `continuity` | K线连续性 | 每天最高价持续高于前日最低价一定比例，K线接续不中断 |
| `sideways` | 横盘震荡 | 振幅小、趋势平坦、K线重叠率高（100分综合评分） |
| `trend-up` | 连续上涨(5日) | `trend.py` 变体：连续 N 天收阳，过滤涨停连板妖股 |
| `trend-down` | 连续下跌(5日) | `trend.py` 变体：连续 N 天收阴，寻找超跌反弹机会 |
| `hammer` | 金针探底 | 近 3 日出现长下影线探底形态 |
| `long_shadow` | 长下影线(10日) | 近 N 日多次出现足够长的下影线 |
| `upward_gap` | 持续推高 | 连续推高/缺口类形态 |

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
python -m scripts.backtest

# 指定策略
python -m scripts.backtest --only overlap,rising,baseline

# 指定日期范围（只统计 N 年至今）
python -m scripts.backtest --start 2024-01-01 --only overlap,rising

# 输出到指定路径
python -m scripts.backtest --out output/my_report.html
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


> **策略代码两处并存，勿混为一谈：**
> - `backtest/engine.py` 的模板策略：由 `python -m scripts.backtest` 统一跑统计报告（上表 12 个 ID）。
> - `scripts/strategies/strategy_01`～`strategy_14`：独立研究页/导航选项卡，由 `python -m scripts.run_all_strategies` 编排。
> 两套实现不合并；周期轮动类（策略 07～14）共用 `backtest.rebalance.RebalanceEngine`。

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
python -m scripts.tools.debug_overlap --code sz002594 --days 3 --pct 2.0

# 茅台，连续5天重叠>3%，真实挂单模式
python -m scripts.tools.debug_overlap --code sh600519 --days 5 --pct 3.0 --realistic --target 1.0
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
python -m scripts.simulations.sim_overlap --code sz002594

# 茅台，100万起步
python -m scripts.simulations.sim_overlap --code sh600519 --capital 1000000 --days 5 --pct 3.0

# 2024年起
python -m scripts.simulations.sim_overlap --code sz002594 --start 2024-01-01
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
python -m scripts.simulations.sim_portfolio

# 100万起步
python -m scripts.simulations.sim_portfolio --capital 1000000

# 调整重叠阈值
python -m scripts.simulations.sim_portfolio --overlap 2.0          # 重叠>2%（更宽松）

# 调整佣金（万分之N）
python -m scripts.simulations.sim_portfolio --commission 2.5         # 万2.5

# 调整挂单目标
python -m scripts.simulations.sim_portfolio --target 2.0             # 挂单+2%
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
python -m scripts.screen --only breakout
```

---

## 七、数据流与服务拓扑

```
外部源（腾讯/AkShare 主源，BaoStock 备源；新浪用于首建/分钟备源）
    │
    ▼
scripts.update_cache → pipeline.daily_update
    阶段：stocks → etfs → index → minute → enrich → validate → news → reports
    │
    ├─ Parquet 事实 ──→ cache/*.parquet (+ cache/features/)
    └─ 资讯缓存   ──→ state/market_news.sqlite3

cache 行情事实
    │
    ├──→ features/*（可复算指标）──→ screen / 决策快照 / 训练防剧透
    ├──→ screen/* → pipeline.runner → output/dashboard.html
    ├──→ backtest/engine.py → scripts.backtest → output/stats_report.html
    ├──→ scripts/strategies/*（独立研究页，与 engine 模板策略分离）
    └──→ scripts/simulations/* → output/*_sim.html

交互服务（同一进程，127.0.0.1:8765）
    scripts.serve → scripts.services.minute_viewer --serve
      入口：minute / grid / trainer / journal / news / watchlist
      日记读写：state/stock_journal.sqlite3

静态报告（另一进程，127.0.0.1:8000）
    scripts.serve → 兼容索引与 output/ 静态 HTML

每日缓存更新是工作日计划任务，不是常驻微服务。
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
