# Tailscale 外网访问

交付日期：2026-09-08。在保留家庭局域网 `--lan` 访问的前提下，用 Tailscale 把家里 Mac 与手机（等设备）组成加密虚拟局域网，从而在公司等外网环境打开交互 Web，而**不必**把 `8765` 端口映射到公网。

相关文档：[15-服务管理](15-服务管理.md)、[22-界面优化与局域网访问](22-界面优化与局域网访问.md)。公网临时隧道方案见 [25-本地股票研究MCP](25-本地股票研究MCP.md)（MCP / Cloudflare，与本文 Web 入口不同）。

## 为什么用 Tailscale

| 方案 | 适用 | 风险 / 成本 |
|---|---|---|
| 家庭 Wi-Fi `192.168.x.x:8765` | 人在家里 | 出不了局域网 |
| **Tailscale（本文）** | 公司手机 / 外网笔记本 | 需同账号设备在线；服务仍跑在家里 |
| Cloudflare Tunnel / frp | 需要普通浏览器直开 `https://…` | 暴露面更大，通常要加鉴权与运维 |
| 路由器端口映射 | 不推荐 | `8765` 无账号保护，不宜裸奔公网 |

交互 Web 当前**没有**登录或访问码（见文档 22）。Tailscale 把可达范围限制在你账号下的设备，比公网端口映射更合适。

## 当前设备与地址（以本机实测为准）

以下为 2026-09-08/09 在 `donggeimac` 上确认过的信息；IP 可能随设备重装或账号策略变化，以 `tailscale status` / `tailscale ip -4` 为准。

| 设备 | Tailscale 主机名 | Tailscale IPv4 | 角色 |
|---|---|---|---|
| 家里 Mac | `donggeimac` | `100.112.224.109` | 跑 `scripts.serve` / dashboard |
| iPhone 13 | `iphone-13` | `100.100.134.108` | 外网浏览端 |

账号标识（状态输出中的登录主体）：`xiaodong.lv.0161@`。

### 常用 URL

外网（手机先打开 Tailscale App，确认 Connected）：

- 选股仪表盘：<http://100.112.224.109:8765/dashboard.html>
- 每日操盘：<http://100.112.224.109:8765/daily_ops.html>
- 市场资讯：<http://100.112.224.109:8765/market_news.html>
- 观察池：<http://100.112.224.109:8765/watchlist.html>

家庭局域网（无需 Tailscale）：

- <http://192.168.1.63:8765/…>（DHCP 变化后以启动日志为准）

本机回环：

- <http://127.0.0.1:8765/…>

把主机名换成 Mac 的 Tailscale MagicDNS 名也可以（若账号已开启 MagicDNS），例如 `http://donggeimac:8765/dashboard.html`；解析失败时退回上面的 `100.x` 地址。

## 前置条件

1. **家里 Mac 开机**，且交互 Web 在监听（`--lan` 或等价地绑定 `0.0.0.0:8765`）。仅监听 `127.0.0.1` 时，Tailscale 网卡也进不来。
2. **Mac 与手机登录同一个 Tailscale 账号**，两端状态均为 Connected。
3. 公司网络若拦截非标准出站端口或 UDP，可能影响直连；Tailscale 一般会回落到 DERP 中继，变慢但仍可能可用。若完全打不开，再考虑 HTTPS 隧道方案。

检查服务是否对所有网卡开放：

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
# 期望类似：TCP *:8765 (LISTEN)
```

启动 / 重启为局域网（含 Tailscale）可访问模式：

```bash
cd /Users/dong_007/D/stock
.venv/bin/python -m scripts.serve restart web --lan --replace-conflicts
.venv/bin/python -m scripts.serve status
```

本机验证 Tailscale 地址可达：

```bash
curl -sI --max-time 5 http://100.112.224.109:8765/dashboard.html | head -5
# 期望 HTTP/1.0 200 OK（或当前服务返回的 200）
```

## Mac 安装与登录（已完成记录）

1. 安装客户端（Homebrew Cask 会下载 pkg；无交互 sudo 时用图形安装器）：
   - `brew fetch --cask tailscale` 后 `open` 缓存的 `.pkg`，或从 [Tailscale 下载页](https://tailscale.com/download) / App Store 安装。
2. 打开 **Tailscale** App，登录；系统提示「允许添加 VPN 配置」时允许。
3. 菜单栏图标显示 Connected 后确认：

```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale status
/Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4
```

CLI 也可能在 `/usr/local/bin/tailscale`（随 App 提供的命令行包装）。

## 手机端用法

1. App Store 安装 **Tailscale**，用与 Mac **相同账号**登录。
2. 打开开关至 Connected（外出时保持 VPN 开着）。
3. Safari / Chrome 访问上文「外网 URL」。可把该链接加到主屏幕便于复用。
4. 家里 Mac 休眠/关机或 `8765` 服务停掉时，页面会打不开——这是预期行为。

## 安全与边界

- Tailscale 只解决**网络可达**，不替代应用层鉴权。当前 8765 仍无密码；请勿把同账号随意分享给他人设备。
- 不要把家里路由器的 `8765` 端口映射到公网「图省事」。
- MCP 的 Cloudflare 临时隧道（文档 25）与 Web 入口独立：隧道 URL 会过期，且面向 MCP Bearer，不适合当手机看板长期地址。
- 个人行情缓存、观察池、日记在 Mac 本地；外网访问等于远程操作家里那台机器上的服务，注意公共 Wi-Fi 上的屏幕与浏览器历史。

## 故障排查

| 现象 | 排查 |
|---|---|
| 手机打不开页面 | 手机 Tailscale 是否 Connected；Mac 是否在线且 Tailscale Connected |
| Mac 在线仍超时 | `lsof` 是否 `*:8765`；用 `--lan` 重启 web；本机 `curl` Tailscale IP |
| 只能家里开、公司不行 | 公司网络策略；换蜂窝数据试一次；看 Tailscale 是否走 DERP |
| IP 变了 | 再跑 `tailscale ip -4`，更新书签；或改用 MagicDNS 主机名 |
| 服务「偶发」没了 | 看 `scripts.serve status`、`output/logs/interactive_web.log`；每日更新后应自动 `--lan` 拉起（见文档 15） |

查看节点与连通：

```bash
tailscale status
tailscale ping iphone-13
# 或
/Applications/Tailscale.app/Contents/MacOS/Tailscale ping iphone-13
```

## 与局域网文档的关系

- [22-界面优化与局域网访问](22-界面优化与局域网访问.md) 描述 `--lan`、`192.168.1.63` 与同网信任模型。
- 本文在同一监听模式上叠加 Tailscale：对 Web 进程而言，Tailscale 网卡只是又一块可达网卡；**无需**为外网再开第二个端口。
- 日常「人在家」继续用局域网 IP；「人在外」用 Tailscale `100.x`（或 MagicDNS）。

## 变更记录

| 日期 | 说明 |
|---|---|
| 2026-09-08 | Mac 安装 Tailscale；与 iPhone 13 同账号组网；确认 `100.112.224.109:8765` 可打开 dashboard |
| 2026-09-09 | 本文档入库 |
