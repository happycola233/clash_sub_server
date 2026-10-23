# Clash Sub Server

一个用于聚合 Clash/Mihomo 订阅源的 Flask + Waitress 服务。

## 特性

- 支持单源、多源、本地 YAML、远程 URL。
- 多源并发拉取，减少等待时间。
- 多源失败策略可配置：严格失败或允许部分成功。
- 远程订阅源可显式走 Mihomo/Clash 代理，不依赖 systemd 继承 shell 环境。
- 配置文件和商业规则文件支持热重载。
- 服务内自动更新 MetaCubeX classical 规则，按优先级去重，失败保留旧文件。

## 项目结构

```text
server.py                         # 入口：创建 app 并用 Waitress 启动
config.example.yaml               # 示例配置，可提交到 GitHub
config.yaml                       # 本地真实配置，默认被 .gitignore 忽略
rules/commercial_rules.list       # 自动生成的商业分流规则
rules/sources.yaml                # 上游名单、目标组与匹配顺序
rules/ip_check.list               # 固定保留的 9 个客户端 IP 检测接口
clash_sub_server/
  app.py                          # Flask app factory 和 HTTP 路由
  config_loader.py                # 配置加载、校验、热重载
  fetcher.py                      # 订阅源加载、代理、多源并发、超时
  parser.py                       # 上游 YAML 解析
  merger.py                       # 多源节点合并、同名节点重命名
  renderer.py                     # 最终 Clash/Mihomo YAML 渲染
  rule_updater.py                  # 下载、校验、去重并原子更新规则文件
  rule_update_service.py           # 随服务启动的后台更新任务
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
- `proxy.enabled`：远程订阅源与规则下载是否显式走代理。
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

如果希望本服务拉取远程订阅源和规则时走 Mihomo/Clash，建议在 `config.yaml` 中显式配置：

```yaml
proxy:
  enabled: true
  http: http://127.0.0.1:7890
  https: http://127.0.0.1:7890
```

这个代理同时用于远程 source 和自动规则更新。本地文件读取不会走代理。

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

`rules/commercial_rules.list` 由 `rules/sources.yaml` 生成。服务端加载时：

- 空行会被忽略。
- 以 `#` 开头的行是维护注释，会被忽略。
- 其他非空行会作为规则原样加入最终订阅配置。
- 程序不会排序、不会去重、不会修改规则文本。

### 更新商业规则

按原来的方式启动订阅服务即可。服务启动后在后台更新一次，之后默认每 24 小时更新，
不需要单独执行命令、添加 crontab 或创建另一个自启动服务。

更新不会让订阅请求等待下载。成功后新规则立即用于后续订阅请求，
即使 `hot_reload.enabled` 为 `false` 也会生效；失败时继续使用旧规则，默认 1 小时后重试。
更新任务每分钟检查一次来源配置和本地名单，修改 `sources.yaml` 或 `ip_check.list` 会提前触发更新。

以下选项都有默认值，旧配置无需补写也会自动更新：

```yaml
rules:
  commercial_rules_path: rules/commercial_rules.list
  auto_update: true
  update_interval: 86400  # 秒
  sources_path: rules/sources.yaml
```

`sources_path` 省略时默认使用商业规则文件同目录的 `sources.yaml`。
下载复用现有的 `proxy` 配置；将 `auto_update` 设为 `false` 可以关闭自动更新。

手动命令仅用于调试或离线生成，正常运行服务不需要使用：

```bash
python -m clash_sub_server.rule_updater
```

手动命令需要代理时使用 `--proxy http://127.0.0.1:7890`，也可以设置 `HTTPS_PROXY`。
只检查是否有更新，使用 `--check`：返回码 0 表示一致，1 表示需要更新，2 表示更新失败。

更新器先确定 `meta` 分支的 commit，再从同一版本下载全部名单。所有名单通过格式校验后，
按 `sources.yaml` 的顺序拼接，相同匹配条件保留最先出现的策略。全部成功才原子替换结果文件；
任何下载或校验失败都会保留旧规则，内容不变时也不会改动文件时间。

使用本地上游仓库时可以离线生成，不会切换或修改上游仓库：

```bash
python -m clash_sub_server.rule_updater --local-repo /path/to/meta-rules-dat
```

指定 `--ref <commit>` 可以重现某个上游版本。需要改变来源时编辑 `rules/sources.yaml`，
不要直接改生成文件；固定的 9 个客户端 IP 检测域名独立保存在 `rules/ip_check.list`。

当前策略：

- 广告规则先于商业服务规则匹配。
- YouTube 先于 Google；GitHub、Xbox 先于 Microsoft。
- X（Twitter）使用 `twitter.list`，Grok 先于 X 匹配，`grok.x.com` 仍归 Grok。
- Gemini 留在 Google，GitHub Copilot 留在 GitHub，Microsoft Copilot 留在 Microsoft。
- Apple Intelligence、Apple TV+ 留在 Apple；Disney 使用 `disney.list`。
- Bilibili 海外只使用 `biliintl.list` 和 `bilibili@!cn.list`。
- 其他 AI 跟随通用代理、直连及兜底规则，不额外维护 AI 总名单或代理组。
- Steam、Xbox、Microsoft、Apple 不拆国内子集；Steam、Xbox 默认选择代理，Microsoft、Apple 默认选择全球直连。
- Netflix、Telegram 和 X 的 IP 名单位于域名规则之后，附加 `no-resolve`，不为 IP 匹配额外触发 DNS 查询。
- iCloud、Apple、Google、Telegram IP 已改由上述名单负责，不再重复接入旧的四个 provider。

客户端仍需按原来的方式刷新订阅才能收到新规则。
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

规则更新和路由回归检查：

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
