# 本地股票研究 MCP

`stock-data` 是项目的本地只读 MCP Server。Codex 通过 STDIO 按需启动它，直接查询
Parquet、SQLite 和白名单研究报告，不经过 8765 HTTP API。
可选的 Streamable HTTP（默认 `127.0.0.1:8766`）仅用于 Cursor / Grok Bot
经反向隧道接入，见下文「Cursor / Grok Bot 接入」。

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

## Secure MCP Tunnel（可选远程访问）

本机以外的 Codex 或 ChatGPT 不能直接启动项目级 STDIO Server。需要远程使用时，可由
OpenAI `tunnel-client` 在这台 Mac 上启动 `stock-data`，再建立只出站的 HTTPS 隧道；
无需开放路由器端口。这不会改变 `stock-data` 的只读边界，这台 Mac、行情缓存和隧道
后台进程也必须保持在线。配置原理与权限要求见
[OpenAI Secure MCP Tunnels](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)。

本机安装与运行文件位于仓库之外：

```text
~/Library/Application Support/stock-data-tunnel/
├── bin/                 # tunnel-client 及随附程序
├── profiles/            # stock-data.yaml，不含明文密钥
└── secrets/             # runtime-api-key，权限必须为 600
```

受管理实例的健康端口由系统在回环地址上动态分配，实际 URL 写入：

```text
~/Library/Application Support/tunnel-client/health/stock-data.url
```

仓库内保留稳定启动入口：

```bash
scripts/tools/run_stock_mcp_stdio.sh   # Codex / OpenAI tunnel-client
scripts/tools/run_stock_mcp_http.sh    # Cursor / Grok Bot（本机 HTTP）
```

隧道配置使用已创建的 `tunnel_id`，运行密钥通过
`file:.../secrets/runtime-api-key` 引用。应在 OpenAI 平台单独创建仅具有
`Tunnels Read` 与 `Tunnels Use` 权限的
[Runtime API Key](https://platform.openai.com/settings/organization/api-keys)；不要复用项目行情密钥，
也不要把密钥粘贴到聊天、Git、YAML 或 shell 历史中。

首次写入密钥时可在本机终端执行以下命令。输入过程不会回显：

```bash
read -s "TUNNEL_RUNTIME_KEY?Runtime API key: "; echo
umask 077
printf '%s' "$TUNNEL_RUNTIME_KEY" > \
  "$HOME/Library/Application Support/stock-data-tunnel/secrets/runtime-api-key"
unset TUNNEL_RUNTIME_KEY
```

随后由 `tunnel-client runtimes connect` 建立受管理的长期运行实例，并用
`tunnel-client runtimes status stock-data --json` 检查状态。不要用 `nohup` 或
`disown` 自行托管。若直连 OpenAI 控制面较慢，可临时设置
`HTTPS_PROXY=http://127.0.0.1:7897`；本地 Clash 端口变化时应以实际端口为准。

健康页面仅监听动态的 `127.0.0.1` 端口，不对局域网或公网开放。远程客户端只能看到
MCP 工具的结构化返回，不能获得本机路径、密钥或任意文件访问能力。

## Cursor / Grok Bot 接入

Codex 在本机用 **STDIO**；Cursor / Grok Bot 的云端后端看不到本机
`/Users/dong_007/D/stock` 缓存，也不能用 `AddMcpServer` 的 `command` 启动本机
Python。需要 **本机 Streamable HTTP + 反向隧道**，再以 `url` 方式接入。

对照：

| 客户端 | 传输 | 说明 |
|---|---|---|
| Codex（本机） | STDIO | `.venv/bin/python -m stock_mcp.server` 或 `scripts/tools/run_stock_mcp_stdio.sh` |
| ChatGPT / OpenAI 平台 Codex | OpenAI `tunnel-client` | 见上一节 Secure MCP Tunnel；**不是** Cursor 用的通道 |
| Cursor / Grok Bot | HTTP + cloudflared quick tunnel | 本机 `127.0.0.1:8766`，公网 `https://….trycloudflare.com/mcp` |

HTTP 边界：

- 默认只绑定 `127.0.0.1`（不要改成局域网网卡）
- 必须带共享 Bearer：环境变量 `STOCK_MCP_TOKEN`，或本地文件
  `~/Library/Application Support/stock-data-tunnel/secrets/cursor-mcp-token`（权限 600）
- 仍安装出站网络拦截与只读语义；**无交易**
- 不取代 8765 / 8000，也不并入 `scripts.serve`

### 1. 生成 Token（只做一次）

```bash
.venv/bin/python -m stock_mcp.server --mint-token
# 轮换：
.venv/bin/python -m stock_mcp.server --mint-token --overwrite-token
```

Token 写入上述 secrets 路径，**不要提交 Git，不要贴进聊天或 commit message**。

### 2. 启动本机 HTTP MCP

```bash
scripts/tools/run_stock_mcp_http.sh
# 等价：
# STOCK_MCP_TOKEN=… .venv/bin/python -m stock_mcp.server --http --host 127.0.0.1 --port 8766
```

MCP 路径：`http://127.0.0.1:8766/mcp`；探活：`http://127.0.0.1:8766/healthz`（无需 Token）。

### 3. 临时公网隧道（cloudflared）

二进制已在仓库外：

```bash
CF_BIN="$HOME/Library/Application Support/stock-data-tunnel/bin/cloudflared"
"$CF_BIN" tunnel --url http://127.0.0.1:8766
```

日志里会出现 `https://<random>.trycloudflare.com`。把该 URL 加上 `/mcp` 交给 Cursor。

### 4. AddMcpServer / 连接器参数

```text
name:    stock-data
url:     https://<random>.trycloudflare.com/mcp
headers:
  Authorization: Bearer <STOCK_MCP_TOKEN>
```

Grok Bot / Cursor 侧用远程 `url` MCP；不要用 `command`（那会在云 box 上跑，读不到本机缓存）。
quick tunnel 地址会变，长期使用需固定隧道或重新配置 URL。本机 HTTP 进程与
cloudflared 都必须保持在线。

## 验收

```bash
# 合约与 STDIO 端到端
python -m pytest -q tests/test_stock_mcp.py

# Streamable HTTP 鉴权挂载冒烟
python -m pytest -q tests/test_stock_mcp_http.py

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
