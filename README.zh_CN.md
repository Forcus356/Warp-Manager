![Warp-Manager](docs/assets/banner.svg)

# Warp-Manager

[English](README.md) · [فارسی](README.fa_IR.md) · [العربية](README.ar_EG.md) · [中文](README.zh_CN.md) · [Español](README.es_ES.md) · [Русский](README.ru_RU.md) · [Türkçe](README.tr_TR.md)

面向 Linux VPS 的 Cloudflare WARP 隧道管理器：独立的 Gemini 与 YouTube 策略、本地 SOCKS5/HTTP 入口、每隧道健康监测，以及带热备的容量扩展。

**0.4.1**

## 要求与安装

需要 root、Ubuntu/Debian x86_64、systemd、/dev/net/tun、互联网与 Python 3.10+。已在 Ubuntu 24.04/Python 3.12 上进行真实 VPS 测试。安装器按需安装 Docker/Compose 和工具，并保留已有 Docker。官方 WARP 客户端在构建镜像时下载，启动时使用 --accept-tos。

从完整压缩包或源码目录安装：

```bash
cd Warp-Manager
sudo bash install.sh
```

将项目上传到 Forcus356/Warp-Manager 的 main 分支后，也可使用独立引导安装器：

```bash
curl -fsSL https://raw.githubusercontent.com/Forcus356/Warp-Manager/main/install_warp_manager.sh -o /tmp/install_warp_manager.sh
sudo bash /tmp/install_warp_manager.sh
```

引导器先把分支或标签解析为不可变 commit，再通过 HTTPS 下载完整源码，并显示 commit 与 ZIP SHA-256。本地源码使用 install.sh。程序不会自动发现最新 GitHub Release。

## 控制台与模式

安装后的命令仍为 warp-pool；warp-google-pool 路径及服务名保持兼容。控制台标签目前为俄语，所有语言使用相同 CLI 参数。终端中无参数运行 install.sh 会打开菜单；t 为模板，Easy/Advanced 可预览、更改或取消草稿。

Gemini 需要匿名生成探测返回 SERVING。REFUSED、CAPTCHA、INCONCLUSIVE 都不算成功。登录账户的实际结果可能不同。

YouTube 要求观察到 Google 地区 RU，并在该独立实例中禁用 Gemini。RU VERIFIED 不保证无广告或视频质量。

```bash
sudo bash install.sh --instance 1 --role gemini --target-gbps 1 --tunnels 12 --impersonate
sudo bash install.sh --instance 2 --role youtube --target-gbps 1 --tunnels 12
```

## 容量与热备

target-gbps 的单位是 Gbit/s：0.1 = 100 Mbit/s；0 表示固定池。先分别测量隧道，再同时测量主池。单独测量之和不能证明已达到总目标。最多 99 个实例、每实例 1–128 个槽位；这些是配置上限，不是 VPS 容量保证。

热备数量为 max(1, floor(N/3))：10 个主隧道需要 3 个热备，30 个需要 10 个。热备也需验证，仅在提升为主隧道后接收新流量，之后自动补充。均衡器依据实测速度和活动/等待连接数分配新连接。单个 TCP 连接始终使用一个隧道。未使用槽位不会启动容器。

WARP 不提供这里所需的国家选择或固定出口服务器 PIN。国家列表过滤的是观察到的 Google 地区。监测、隔离、退避与轮换限制分别作用于每条隧道。

## 常用命令

```bash
warp-pool
warp-pool --all status
warp-pool --instance 1 check all --full
warp-pool --instance 1 speed all
warp-pool --instance 1 logs -n 100
warp-pool --instance 1 capacity --gbps 1 --max-tunnels 12
warp-pool --instance 1 capacity --gbps 0 --max-tunnels 3
```

check/search/speed/rotate 只是提交队列请求；退出码 0 表示接受，不代表探测成功。请查看 status/logs。--all 仅支持 status/logs/config/xray/update。安装器 --interval 与菜单使用分钟，JSON 时间字段使用秒。

## Xray / 3x-ui

路径为 Xray → 本地 SOCKS5 → WARP → 互联网。导出不会修改面板。保留原有默认 outbound 在首位，并把服务规则放在广泛规则之前。文档提供主机/Docker、域名转发、远程 DNS，以及仅针对选定服务的 UDP/443 阻断说明。不支持 UDP ASSOCIATE。

```bash
warp-pool xray-routing --youtube-instance 2 --gemini-instance 1
```

[Xray JSON + sniffing](docs/xray/README.md)

## 更新与卸载

```bash
warp-pool --instance 1 update /root/Warp-Manager
warp-pool --all update /root/Warp-Manager-v0.4.1.zip --sha256 "$EXPECTED_SHA256"
warp-pool --instance 2 uninstall
```

更新保留配置、端口与注册卷。HTTPS ZIP 更新必须提供预期 SHA-256。默认卸载保留本地注册数据；--purge-profiles 才删除这些卷。--purge-deps 仅请求清理本项目安装且已无其他使用者的依赖。

## 测试与实测限制

在 1 vCPU/2 GiB VPS 上，8 条真实 WARP 在 8/32/64 客户端测试中无错误。8 客户端时，1/2/4/8 隧道总速率约为 85/132/165/172 Mbit/s。CPU 达到 100%，TLS 负载生成器也运行于同一 CPU。未验证千兆速率或 128 个真实客户端无错误。观察到两次 Gemini SERVING，但没有建立稳定且有完整热备的 Gemini 池。YouTube 恢复测试在 100 Mbit/s 控制目标下通过。

```bash
python3 scripts/check_project.py
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py
```

[Test methodology and results](docs/testing/RESULTS.md)

## 文档与许可证

详细参考文档为英语/俄语。公开项目不包含真实 VPS 地址、SSH 凭据或注册配置。控制台预览使用合成示例状态，不是实际可用出口的证明。

[Documentation](docs/README.md) · [Configuration](docs/CONFIGURATION.md) · [Commands](docs/COMMANDS.md) · [Operations](docs/OPERATIONS.md) · [FAQ](docs/FAQ.md) · [Publishing](docs/PUBLISHING.md) · [Security](SECURITY.md)

非官方管理器，采用 GPL-3.0-only。单独下载的 Cloudflare 客户端遵循其自身条款。

[LICENSE](LICENSE) · [Attribution](THIRD_PARTY_NOTICES.md) · [Upstream notices](UPSTREAM_NOTICES.md)
