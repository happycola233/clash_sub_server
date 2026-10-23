# Clash Sub Server

一个用于聚合 Clash/Mihomo 订阅源的 Flask + Waitress 服务。

## 特性

- 支持单源、多源、本地 YAML、远程 URL。
- 多源并发拉取，减少等待时间。
- 多源失败策略可配置：严格失败或允许部分成功。
- 远程订阅源可显式走 Mihomo/Clash 代理，不依赖 systemd 继承 shell 环境。
- 配置文件和商业规则文件支持热重载。
- 服务分流直接引用 MetaCubeX GeoSite / GeoIP 数据库，由客户端内核每天更新。
- BT/PT 站点及常见 BT 下载进程直连。

## 项目结构

```text
server.py                         # 入口：创建 app 并用 Waitress 启动
config.example.yaml               # 示例配置，可提交到 GitHub
config.yaml                       # 本地真实配置，默认被 .gitignore 忽略
rules/commercial_rules.list       # Geo 分组到代理组的映射及 9 条 IP 检测规则
clash_sub_server/
  app.py                          # Flask app factory 和 HTTP 路由
  config_loader.py                # 配置加载、校验、热重载
  fetcher.py                      # 订阅源加载、代理、多源并发、超时
  parser.py                       # 上游 YAML 解析
  merger.py                       # 多源节点合并、同名节点重命名
  renderer.py                     # 最终 Clash/Mihomo YAML 渲染
  userinfo.py                     # Subscription-Userinfo 处理
```

## 安装

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## 配置

首次使用：

```bash
cp config.example.yaml config.yaml
```

然后编辑 `config.yaml`。

常用配置：

- `routes.subscription_paths`：订阅入口路径，例如 `/c-sub`。
- `source_request.connect_timeout`：建立连接超时。
- `source_request.read_timeout`：读取响应超时。
- `source_request.overall_timeout`：多源并发整体等待上限。
- `source_request.allow_partial_failure`：多源部分失败时是否继续使用成功源。
- `proxy.enabled`：远程订阅源下载是否显式走代理。
- `proxy.http` / `proxy.https`：HTTP/HTTPS 请求使用的代理地址。
- `tokens`：访问 token 与订阅源配置。

示例：

```yaml
tokens:
  example_token:
    source:
      - ref: https://example.com/sub.yaml
        tag: source1
      - ref: ./local-sub.yaml
        tag: local
    userinfo_mode: custom
    subscription_userinfo: "upload=0; download=0; total=1099511627776; expire=253402185600"
```

访问：

```text
http://127.0.0.1:2233/c-sub?token=example_token
```

## 多源失败策略

默认使用严格模式：

```yaml
source_request:
  allow_partial_failure: false
```

严格模式下，只要任意一个 source 加载失败，本次订阅请求就返回错误。

如果希望部分 source 失败时仍然返回成功源，可以改为：

```yaml
source_request:
  allow_partial_failure: true
```

## 代理说明

`clashproxy on` 通常只影响交互式 shell。systemd 启动的服务不一定继承这些环境变量。

如果希望本服务拉取远程订阅源时走 Mihomo/Clash，建议在 `config.yaml` 中显式配置：

```yaml
proxy:
  enabled: true
  http: http://127.0.0.1:7890
  https: http://127.0.0.1:7890
```

这个代理用于远程 source。本地文件读取不会走代理，客户端的 Geo 数据库下载也不使用此设置。

## 热重载

默认开启：

```yaml
hot_reload:
  enabled: true
```

程序会在每次请求进入时检查 `config.yaml` 和 `rules/commercial_rules.list` 的 mtime。文件变更后会自动重新加载。

如果新配置写错，程序会保留当前有效配置继续服务，并在日志里记录错误。

注意：`server.host` 和 `server.port` 是 Waitress 启动参数，修改后需要重启服务才会改变监听端口。

## 商业规则文件

`rules/commercial_rules.list` 直接维护 `GEOSITE` / `GEOIP` 到代理组的映射，
以及固定的 9 条客户端出口 IP 检测规则。服务端加载时：

- 空行会被忽略。
- 以 `#` 开头的行是维护注释，会被忽略。
- 其他非空行会作为规则原样加入最终订阅配置。
- 程序不会排序、不会去重、不会修改规则文本。

### 数据库更新

生成的订阅使用 Mihomo 内核，并显式配置：

```yaml
geodata-mode: true
geo-auto-update: true
geo-update-interval: 24  # 小时
geox-url:
  geoip: https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.dat
  geosite: https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geosite.dat
  mmdb: https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.metadb
  asn: https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/GeoLite2-ASN.mmdb
```

域名使用 `geosite.dat`，服务 IP 分组使用 `geoip.dat`。数据库下载、定时更新由客户端内核负责，
服务端只维护分流映射。无需额外命令或定时任务；数据库更新也不需要重新获取订阅。
如果客户端的全局设置覆盖订阅配置，应以实际生效的配置为准。

服务端 `rules` 配置只需保留 `commercial_rules_path`；旧的 `auto_update`、
`update_interval`、`sources_path` 已不再使用，可以删除。

当前策略：

- 广告规则先于商业服务规则匹配。
- YouTube 先于 Google；GitHub、Xbox 先于 Microsoft。
- X（Twitter）使用 `GEOSITE,twitter`，Grok 先于 X 匹配，`grok.x.com` 仍归 Grok。
- Gemini 留在 Google，GitHub Copilot 留在 GitHub，Microsoft Copilot 留在 Microsoft。
- Apple Intelligence、Apple TV+ 留在 Apple；Disney 使用 `GEOSITE,disney`。
- Bilibili 海外只使用 `GEOSITE,biliintl` 和 `GEOSITE,bilibili@!cn`。
- 其他 AI 跟随通用代理、直连及兜底规则，不额外维护 AI 总名单或代理组。
- Steam、Xbox、Microsoft、Apple 不拆国内子集；Steam、Xbox 默认选择代理，Microsoft、Apple 默认选择全球直连。
- Netflix、Telegram 和 X 的 IP 名单位于域名规则之后，附加 `no-resolve`，不为 IP 匹配额外触发 DNS 查询。
- iCloud、Apple、Google、Telegram IP 已改由上述名单负责，不再重复接入旧的四个 provider。

BT 直连规则位于服务分流和通用代理规则之前（现有 UDP 443 拦截、广告规则仍优先）：

```yaml
- GEOSITE,category-public-tracker,DIRECT
- GEOSITE,category-pt,DIRECT
- PROCESS-NAME-REGEX,(?i)^(qbittorrent(-nox)?|transmission.*|deluge.*|aria2c?|motrix|utorrent|bitcomet(_x64)?)(\.exe)?$,DIRECT
```

PT 名单也会让浏览器访问对应站点时直连；进程规则会直连这些下载器的全部流量，
包括 aria2、Motrix 的普通 HTTP 下载。进程匹配适用于内核能够识别的本机进程，无法识别其他设备的进程。

修改分流映射后，客户端仍需刷新订阅。
修改 `renderer.py` 的组名、默认选项或规则顺序后，需要重启服务进程。

## 运行

开发或简单部署：

```bash
./venv/bin/python server.py
```

systemd 示例：

```ini
[Unit]
Description=Clash Subscription Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/clash_sub_server
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/clash_sub_server/venv/bin/python /opt/clash_sub_server/server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## 检查

路由与订阅接口回归检查：

```bash
python -m unittest discover -s tests -v
```

配置解析：

```bash
python - <<'PY'
from clash_sub_server.config_loader import load_config
cfg = load_config("config.yaml")
print(len(cfg.tokens), "tokens")
print(len(cfg.commercial_rules), "rules")
PY
```

接口测试：

```bash
curl -L "http://127.0.0.1:2233/c-sub?token=example_token"
```
