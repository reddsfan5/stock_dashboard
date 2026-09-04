# scripts 命令分层

`scripts/` 按“执行范围”和“职责”分层。顶层只放会编排多个模块的全局入口；生成单个页面或完成单项研究的脚本全部放入对应子目录。

```text
scripts/
├── update_cache.py          # 全局：更新日线、指数、分钟线和市场报告
├── screen.py                # 全局：运行全部选股模块
├── backtest.py              # 全局：运行统计回测策略集合
├── run_all_strategies.py    # 全局：并行运行全部独立策略研究
├── strategies/              # 单项：strategy_01 ~ strategy_14
├── simulations/             # 单项：资金与交易流程模拟
├── research/                # 单项：市场统计研究
├── reports/                 # 基础设施：报告和导航生成
├── services/                # 服务：分时、网格、T+1训练、日记与市场资讯
└── tools/                   # 开发工具：逐笔调试、参数扫描
```

## 使用原则

- 想“一次把一类任务全部跑完”，使用 `scripts/` 顶层入口。
- 想“只更新某一个选项卡”，进入对应子目录运行单项脚本。
- 推荐从项目根目录用 `python -m` 运行，模块路径能直接体现所属层级。

```bash
# 全局编排
python -m scripts.screen
python -m scripts.backtest
python -m scripts.run_all_strategies
python -m scripts.update_cache

# 单项功能
python -m scripts.strategies.strategy_08_etf_momentum
python -m scripts.simulations.sim_portfolio
python -m scripts.research.weekday_stats
python -m scripts.research.backtest_market_proverbs     # 市场口诀前七条事件回测
python -m scripts.research.backtest_slow_rise           # 缓涨3～5日开盘买入、5日止盈回测
python -m scripts.research.backtest_screen_to_trade     # 选股信号→次日买入持有（基线对照）
python -m scripts.research.optimize_intraday_grid          # 520500 最近10日网格调参
python -m scripts.serve                                    # 一键启动全部 HTTP 服务
python -m scripts.serve start trainer                      # 单独启动训练入口
python -m scripts.serve start news                         # 单独启动市场资讯入口
python -m scripts.serve status                             # 查看服务和定时任务
python -m scripts.tools.debug_overlap --code sh600519
```

`data/`、`screen/`、`backtest/` 和 `pipeline/` 是可复用业务代码；`scripts/` 仅负责命令入口和任务编排，不在这里继续堆积底层实现。

策略 07～14 的周期轮动统一由 `backtest.rebalance.RebalanceEngine` 执行，
`scripts/strategies/rebalance_utils.py` 只保留 CLI 参数与报告适配；旧的
`scripts.simulations.sim_etf_momentum` 是策略 08 的兼容入口，不再维护第二套实现。

分时服务同时提供 `minute_view.html` 行情查询和 `grid_simulator.html` T+0
网格逐分钟回放；两者共用本地分钟缓存和 8765 端口。独立分时页和选股日记内嵌
分时都可在固定全天时间轴上暂停、单步、调速和重新播放，未播放区间不会泄露价格。
网格页可切换银河风格的
成交驱动型（双侧预埋、占用资券）与到价触发型（触价报单、不预占资券），
固定全天坐标轴后让行情曲线从左向右推进。
到价触发型还可动态演示累计反弹/回落、保底价、触发后或全成后更新基准、
排队限价与自动撤单；这些盘口行为使用分钟 OHLC 做可解释近似。

同一服务还会生成 `trading_trainer.html`。训练器在服务端保存会话，浏览器每次只能取得
已经播放的分钟线；日 K 的当日蜡烛由这些已揭示分钟实时合成。手动买卖统一走执行模型，
当天买入仓位锁定到下一交易日，适合练习入场、离场和交易理由复盘。

统一入口 `python -m scripts.serve` 可一键启动交互 Web 与静态报告服务；也可用
`start minute`、`start grid` 或 `start trainer` 按业务入口启动。三者共用 8765 的同一
进程，避免重复加载分钟索引。完整命令和端口冲突处理见
[`docs/15-服务管理.md`](../docs/15-服务管理.md)。

网格参数的离线研究入口是 `scripts/research/optimize_intraday_grid.py`。它默认读取
520500 最近 10 个有数据的交易日，前 7 日搜索、后 3 日验证，同时比较成交驱动型
与到价触发型。详细术语、参数设计与输出解释见 [`docs/11-网格交易.md`](../docs/11-网格交易.md)。

## 编排关系

```text
update_cache ──→ pipeline.daily_update
  ├─ stocks → etfs → index → minute
  ├─ validate（新鲜度/覆盖率门禁）
  └─ reports.gen_market ──→ reports.gen_index + reports.gen_mobile

screen ──→ pipeline.runner ──→ screen/* ──→ output/dashboard.html
backtest ──→ backtest/* ──→ output/stats_report.html
run_all_strategies
  ├─ strategies/* + research/backtest_break_resume + research/backtest_market_proverbs
  ├─ research/backtest_slow_rise
  └─ reports.gen_index + reports.gen_mobile
```

这里有两个容易混淆但含义不同的“策略”概念：

- `screen/` 和 `backtest/` 保存可复用的业务实现，由顶层管线统一发现或注册。
- `scripts/strategies/` 保存会独立生成一个策略研究页面的命令，每个文件对应导航页里的一个策略选项卡。

每日更新的阶段状态写在 `cache/daily_update_status.json`。常用诊断命令：

```bash
python -m scripts.update_cache --validate-only
python -m scripts.update_cache --only minute,validate --target-date 2026-08-27
```

## 新脚本放哪里

| 新功能 | 位置 | 判断标准 |
|---|---|---|
| 新选股算法 | `screen/` | 可由 `scripts.screen` 自动发现并作为选项卡运行 |
| 新统计回测算法 | `backtest/` | 可由 `scripts.backtest` 注册并统一生成报告 |
| 新独立策略页 | `scripts/strategies/` | 单独运行、单独生成一个策略 HTML |
| 新资金模拟 | `scripts/simulations/` | 包含仓位、成交、费用或资金曲线模拟 |
| 新专题统计 | `scripts/research/` | 一次性或独立的市场研究/验证 |
| 新页面生成器 | `scripts/reports/` | 只负责聚合结果和生成展示页面 |
| 新常驻 HTTP 功能 | `scripts/services/` | 需要保持进程运行并提供服务 |
| 新调试/参数扫描 | `scripts/tools/` | 面向开发者，不是最终业务选项卡 |
| 新全局编排 | `scripts/` 顶层 | 同时调度多个模块或多个输出 |

迁移旧命令时，将 `python scripts/foo.py` 改为对应的模块命令。例如服务现在使用：

```bash
python -m scripts.serve start web
```
