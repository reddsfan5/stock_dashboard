# 本地股票研究 MCP

`stock-data` 是项目的本地只读 MCP Server。Codex 通过 STDIO 按需启动它，直接查询
Parquet、SQLite 和白名单研究报告，不经过 8765 HTTP API，也不监听新端口。

## 定位与安全边界

- 数据来自本项目本地缓存，不代表实时行情。
- 只读：不更新行情、不抓取资讯、不写日记、不推进训练、不生成报告、不提交委托。
- MCP 子进程安装 IPv4/IPv6 网络连接拦截；资讯只调用 `cached_day()`。
- SQLite 使用 `mode=ro` 和 `PRAGMA query_only=ON`。
- 历史查询严格遵守 `as_of`；缺数据返回 `missing/partial`，不会回退到当前数据。
- 当前研究上下文会明确标为 `current_research_context_only`，不能用于无剧透训练。
- 当前没有逐笔缓存，因此不提供逐笔工具，分钟 K 也不会被包装成逐笔数据。
- 不读取 `config/openai.yaml`，不接受 SQL、Shell、URL、文件路径或动态模块名。
- 实盘账户与交易能力将来必须使用独立 Server，不能加在 `stock-data` 上。

## 运行环境

项目目标版本固定为 Python 3.12：

```bash
python --version
python -m pip check
python -m pytest -q
```

运行依赖在 `requirements.txt`，测试依赖在 `requirements-dev.txt`。本机 Intel macOS
使用 Homebrew 安装时会因 Xcode 版本要求进入源码构建，因此当前候选环境由 Conda 创建：

```bash
/opt/anaconda3/bin/conda create --prefix .venv-py312 python=3.12 pip -y
.venv-py312/bin/python -m pip install -r requirements-dev.txt
```

验收通过后，原 `.venv` 先改名保留，再把 `.venv-py312` 克隆/切换为正式 `.venv`。
不要删除旧环境，确认服务和定时任务稳定后再清理。

本次迁移已完成：正式 `.venv` 为 Python 3.12.11；原 Python 3.9 环境保存在
`.venv-py39-backup-20260907`，已验收候选环境仍保存在 `.venv-py312`，等待稳定确认。

## 工具目录

| 工具 | 用途 | 上限与时间语义 |
|---|---|---|
| `get_data_status` | 更新状态、日期范围、行数、标的数、新鲜度 | 当前缓存状态 |
| `search_symbols` | 股票、ETF、指数代码/名称搜索 | 最多 50 项，不扫大行情 |
| `get_catalog` | 数据集、指标、策略、报告目录 | 当前代码目录 |
| `get_daily_bars` | 单标的日 K | 默认 120、最多 500；非交易日向前解析 |
| `get_minute_bars` | 单标的单日分钟 K | 必须指定真实日期；最多 500；按 `as_of` 截断 |
| `get_feature_snapshot` | 量价、流动性、风险、估值截面 | 最多 50 个标的；只用截止日以前数据 |
| `run_screener` | 执行 `PIPELINE_META + find_all` 策略 | 最多 100 项；参数必须由策略声明 |
| `get_market_context` | A 股宽基和海外市场情境 | 按日期和时刻防剧透 |
| `get_market_news` | 早盘、午间、收盘缓存资讯 | 最多 100 条；不联网刷新 |
| `get_symbol_context` | 观察池、日记、训练、假设、最新命中 | 仅当前研究上下文 |
| `get_research_artifacts` | 白名单 JSON/HTML 报告 | JSON 摘要；HTML 返回 8000 服务链接 |

每个成功响应都包含：

```text
schema_version / summary / request / requested_as_of / resolved_as_of
temporal_scope / freshness / provenance / warnings / pagination / data
```

字段使用稳定英文键；中文含义和单位从 `get_catalog(section="features")` 查询。
`NaN`、`NaT` 和无穷值统一返回 `null`，时间使用 ISO 8601，时区为
`Asia/Shanghai`。

## 证券标识

统一解析器位于 `data/instruments.py`：

- 股票：`stock:sh600519`
- ETF：`etf:sh520500`
- 指数：`index:sh000001`

可以输入 `600519`、`sh600519`、`520500`。例如 `000001` 同时可能指平安银行和上证
指数，解析器会返回两个候选，调用者必须改用完整标识，不会静默猜测。

## Codex 接入

项目配置位于 `.codex/config.toml`，命令为：

```bash
.venv/bin/python -m stock_mcp.server
```

配置中 `required=false`，因此 MCP 故障不会阻止 Codex 打开项目；`enabled_tools` 固定
11 个只读工具白名单。全局工具超时设为 120 秒以容纳全市场选股，普通查询本身按
30 秒内完成设计。日线、分钟线和选股另设输出 token 上限。

`stock-data` 不属于 `scripts.serve`：8765/8000 是 HTTP 服务，MCP 的生命周期由
Codex 持有。修改 `.codex/config.toml` 后需要重新打开项目或重启 Codex 任务，让宿主
重新读取配置。

## 验收

```bash
# 合约与 STDIO 端到端
python -m pytest -q tests/test_stock_mcp.py

# 11 个工具调用前后正式缓存 SHA-256 / 大小 / mtime 审计
python -m stock_mcp.audit

# 全项目回归
python -m pytest -q

# 服务入口与依赖
python -m pip check
python -m scripts.serve status
```

真实数据回归固定检查：

```python
get_minute_bars("etf:sh520500", "2026-08-25", as_of="10:15")
get_daily_bars("sh600519", end="2026-08-25")
get_market_news("2026-08-25", as_of="10:15")
get_market_context("2026-08-25", as_of="10:15")
```

第一项的最后时间不得晚于 10:15；资讯和市场情境也必须遵守相同截止点。指定不存在
分钟缓存的日期时必须返回空数据和 warning，不能自动换日。

## 扩展规则

新增工具时依次修改 `contracts → repositories → research_service → server`，并同时：

1. 提供临时 Parquet/SQLite 测试；
2. 证明调用前后缓存哈希和修改时间不变；
3. 声明 `read_only_hint=true`、`idempotent_hint=true`、
   `destructive_hint=false`、`open_world_hint=false`；
4. 加入 `.codex/config.toml` 的 `enabled_tools` 白名单；
5. 给出稳定英文字段、中文目录定义、来源和缺失语义。

未来 iFinD/Longbridge 仅可作为行情适配器单独评估。任何账户查询和交易权限都不属于
本 Server 的扩展范围。
