# Clash Sub Server

一个用于聚合 Clash/Mihomo 订阅源的 Flask + Waitress 服务。

## 特性

- 支持单源、多源、本地 YAML、远程 URL。
- 多源并发拉取，减少等待时间。
- 多源失败策略可配置：严格失败或允许部分成功。
- 远程订阅源可显式走 Mihomo/Clash 代理，不依赖 systemd 继承 shell 环境。
- 配置文件和商业规则文件支持热重载。

## 项目结构

```text
server.py                         # 入口：创建 app 并用 Waitress 启动
config.example.yaml               # 示例配置，可提交到 GitHub
config.yaml                       # 本地真实配置，默认被 .gitignore 忽略
rules/commercial_rules.list       # 商业规则与维护注释
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
- `proxy.enabled`：远程订阅源是否显式走代理。
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

如果希望本服务拉取远程订阅源时一定走 Mihomo/Clash，建议在 `config.yaml` 中显式配置：

```yaml
proxy:
  enabled: true
  http: http://127.0.0.1:7890
  https: http://127.0.0.1:7890
```

这个代理只作用于远程 source。本地 YAML source 不会走代理。

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

`rules/commercial_rules.list` 用于维护商业分流规则：

- 空行会被忽略。
- 以 `#` 开头的行是维护注释，会被忽略。
- 其他非空行会作为规则原样加入最终订阅配置。
- 程序不会排序、不会去重、不会修改规则文本。

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
