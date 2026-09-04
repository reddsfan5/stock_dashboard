# A股量化选股系统 — 文档索引

## 系统概览

本系统是一套本地 A 股量化选股与回测工具链，覆盖：

数据管理（Parquet 行情 + SQLite 日记/资讯）→ 特征计算 → 多维度选股 → 历史回测与独立策略研究 → 资金模拟 → 交互 Web（分时/网格/T+1 训练/日记/资讯/观察池/标的上下文）→ 静态报告输出。

不是微服务架构：交互功能共享一个 `127.0.0.1:8765` 进程；静态报告另开 `127.0.0.1:8000`；每日缓存更新是工作日计划任务。

命令入口分层与归类规则见 [`scripts/README.md`](../scripts/README.md)；服务启停见 [15-服务管理](15-服务管理.md)。

## 文档导航

| 文档 | 内容 | 适合 |
|------|------|------|
| [01-快速开始](01-快速开始.md) | 环境搭建、首次运行、核心概念 | 新用户 |
| [02-数据层](02-数据层.md) | Parquet 行情、SQLite 日记/资讯、多数据源 | 需要了解数据来源 |
| [03-选股系统](03-选股系统.md) | 选股模块与管线自动发现 | 选股使用者 |
| [04-回测系统](04-回测系统.md) | 统计回测策略（scripts.backtest）详解 | 策略研究者 |
| [05-模拟交易](05-模拟交易.md) | 3 个资金模拟引擎 | 实盘前验证 |
| [06-管线编排](06-管线编排.md) | 自动发现、并行执行、报告生成 | 系统开发者 |
| [07-配置参考](07-配置参考.md) | 所有可配置参数一览 | 调参优化 |
| [08-扩展开发](08-扩展开发.md) | 添加新模块/策略/模拟引擎指南 | 开发者 |
| [09-策略研究](09-策略研究.md) | 8个策略的设计、回测和盈亏分析 | 策略研究 |
| [10-库与生态](10-库与生态.md) | 依赖技术栈与值得研究的量化库生态 | 技术选型 |
| [11-网格交易](11-网格交易.md) | 网格术语、双模式逻辑、参数设计与520500近10日优化 | 网格研究者 |
| [12-网格参数设计](12-网格参数设计.md) | 每项设置的优势、缺陷、危险组合与设计模板 | 参数设计者 |
| [13-回测可信度与架构演进](13-回测可信度与架构演进.md) | 盯市、费用、复现、绩效口径、研究到实盘门禁 | 系统架构与研究者 |
| [14-T+1交易训练](14-T+1交易训练.md) | 无剧透日K/分时回放、手动买卖和复盘方法 | 交易训练者 |
| [15-服务管理](15-服务管理.md) | 服务拓扑、单独/一键启停、状态检查与冲突修复 | 系统维护者 |
| [16-指标与特征体系](16-指标与特征体系.md) | 量比口径、特征分层、风险与流动性指标、缓存失效机制 | 量化开发者 |
| [17-市场口诀回测](17-市场口诀回测.md) | 前七条口诀的量化定义、对照基线、结果口径与复现实验 | 策略研究者 |
| [18-缓涨止盈策略回测](18-缓涨止盈策略回测.md) | 近30日相对低位缓涨3～5日、开盘买入、T+1止盈和第五日退出的可执行验证 | 策略研究者 |
| [19-选股日记](19-选股日记.md) | 动态日K标记、追加式决策时间线、查询与备份 | 选股与复盘使用者 |
| [20-市场资讯复盘](20-市场资讯复盘.md) | 同花顺重要资讯时间轴、防剧透查看与消息影响记录 | 模拟交易与复盘使用者 |
| [21-系统胶合](21-系统胶合.md) | 标的上下文、每日清单、假设生命周期 | 日常串联使用者 |

## 项目结构

```
stock/
├── data/           # 数据层：日K/ETF/指数/分钟/估值、源适配、日记与资讯
├── features/       # 特征层：可复算指标与版本化缓存
├── screen/         # 选股层：PIPELINE_META 自注册模块
├── backtest/       # 回测层：统计模板策略、网格、模拟、训练状态机
├── pipeline/       # 编排层：模块发现、每日更新、报告生成
├── scripts/        # 命令入口（编排 / strategies / simulations / research / reports / services / tools）
├── config/         # YAML 配置
├── cache/          # Parquet 行情与特征缓存、更新状态、服务进程记录
├── state/          # SQLite：日记 / 资讯 / 训练 / 观察池 / 假设（个人数据）
├── tests/          # pytest
├── demos/          # 库演示（非业务入口）
├── output/         # HTML / CSV / logs
└── docs/           # 文档（你在这里）
```

## 快速命令

```bash
# 更新数据（日线/ETF/指数/分钟/补齐/校验/资讯/报告）
python -m scripts.update_cache
python -m scripts.update_cache --validate-only

# 选股
python -m scripts.screen                        # 全部模块
python -m scripts.screen --only sideways        # 只看横盘

# 回测（engine 模板策略）与独立策略研究
python -m scripts.backtest                      # 统计回测 12 策略
python -m scripts.backtest --only overlap,rising --start 2024-01-01
python -m scripts.run_all_strategies            # strategy_01..14 等研究页
python -m scripts.research.backtest_market_proverbs
python -m scripts.research.backtest_slow_rise

# 模拟
python -m scripts.simulations.sim_portfolio --capital 100000 --target 2.0
python -m scripts.simulations.sim_dip_buy --capital 100000 -m 2.0

# 服务（交互 Web 8765 + 静态报告 8000；非微服务）
python -m scripts.serve                         # 一键启动
python -m scripts.serve start journal           # 日记等业务入口共用 web 进程
python -m scripts.serve status
```

## 数据流

```
腾讯/AkShare（主）+ BaoStock（备）+ 新浪（首建/分钟备）
        │
        ▼
scripts.update_cache → pipeline.daily_update
        │
        ├─→ cache/*.parquet（股票/ETF/指数/分钟/估值/行业）
        ├─→ cache/features/（版本化特征）
        └─→ state/market_news.sqlite3（资讯；日记另见 journal）

cache 行情 ──→ features/* ──→ screen/* ──→ pipeline.runner ──→ output/dashboard.html
           ├──────────────→ backtest/engine.py ──→ scripts.backtest ──→ stats_report.html
           ├──────────────→ scripts/strategies/*（独立研究页，与 engine 分离）
           └──────────────→ scripts/simulations/* ──→ output/*_sim.html

scripts.serve
  ├─ 127.0.0.1:8765  同一进程：minute / grid / trainer / journal / news
  │                    journal → state/stock_journal.sqlite3
  └─ 127.0.0.1:8000  静态报告索引（output/）
```
