# 测试网络带宽（testing internet bandwidth）

`cmcc_broadband_diag.py` 是一个无第三方依赖的检测程序，起源是用来给移动宽带“智修精灵提示存在群障、线路状态异常”收集证据。

先说明结论：`存在群障` 通常不是家里电脑能修好的问题，多数是运营商接入网、光缆、OLT、分光器、端口或区域设备故障。家里能做的是排除 Wi-Fi/路由器/光猫本地问题，并把证据交给 10086 或装维师傅。

## 使用

在本目录执行：

```bash
python3 cmcc_broadband_diag.py --app-group-fault
```

故障正在发生时，建议跑更完整的版本：

```bash
python3 cmcc_broadband_diag.py --app-group-fault --deep
```

程序会在屏幕输出结论，并保存到 `reports/`：

- `cmcc_broadband_diag_时间.txt`：给自己、客服、装维师傅看的文本报告
- `cmcc_broadband_diag_时间.json`：原始检测数据

## 测下载、上载速度

在 macOS 上可以直接用系统自带工具：

```bash
networkQuality -s
```

在 Rocky Linux/Linux 上，系统没有 `networkQuality`。脚本会自动优先调用 Ookla 官方 `speedtest`，其次调用 `speedtest-cli`：

```bash
command -v speedtest || command -v speedtest-cli
```

也可以用本目录脚本统一生成报告：

```bash
python3 cmcc_broadband_diag.py --speedtest
```

如果 Rocky Linux 输出“没有可用测速工具”，需要先安装 `speedtest` 或 `speedtest-cli`，或者用运营商 App/测速网站交叉测试。

输出里的 `下载速度` 和 `上载速度` 会同时显示 `Mbps` 和 `MB/s`。运营商套餐通常写的是 `Mbps`，下载软件常显示 `MB/s`，换算关系是：

```text
1 MB/s = 8 Mbps
```

参考值：

- 100M 宽带：理论下载约 `12.5 MB/s`
- 300M 宽带：理论下载约 `37.5 MB/s`
- 500M 宽带：理论下载约 `62.5 MB/s`
- 1000M 宽带：理论下载约 `125 MB/s`

测速建议：

1. 优先用网线直连路由器 LAN 口或光猫 LAN 口。
2. 关闭网盘同步、视频、游戏更新、下载器、代理/VPN。
3. 同一位置连续测 2 到 3 次，取中位数。
4. Wi-Fi 测速明显偏低时，优先怀疑无线信号、频段、路由器性能或网卡规格。

## 怎么看结果

- 如果默认网关也丢包或不可达：优先排查 Wi-Fi、路由器、网线、光猫 LAN 口。
- 如果默认网关正常，但多个公网 IP、TCP、网页都失败：高度疑似移动宽带线路或运营商侧故障。
- 如果公网 IP 正常，但域名解析失败：疑似 DNS 故障，可临时改 DNS 为 `223.5.5.5`、`119.29.29.29` 或 `114.114.114.114`。
- 如果脚本基本正常但 App 仍显示群障：仍以 App 群障为准，可能是间歇性故障或部分路由/区域问题，建议故障时再跑一次 `--deep`。

## 给 10086 的话术

可以这样说：

> 中国移动 App 智修精灵提示“存在群障/线路状态异常”。我这边已经重启过光猫和路由器，并用脚本检测过。请帮我查这个宽带账号的 OLT/端口状态、光衰、PON 注册、区域群障和上联出口。

如果光猫 `LOS` 红灯、`PON` 长时间闪烁，直接补充：

> 光猫 LOS/PON 状态异常，请按光路故障派单。

## 家里先做的最小排查

1. 光猫和路由器断电 30 秒。
2. 先开光猫，等 PON/Internet 稳定，再开路由器。
3. 检查光纤别弯折，光猫光纤头别松。
4. 用网线直连路由器 LAN 口跑一次脚本，避免 Wi-Fi 干扰。
5. 如果 App 仍显示群障，不要反复恢复出厂设置，直接报修。
