"""最终 Clash/Mihomo 配置渲染。

这个模块只负责把“已经加载好的节点 + 已加载好的商业规则”组装成最终 YAML。
"""

from __future__ import annotations

from typing import Any

import yaml


def build_final_config(proxies: list[dict[str, Any]], commercial_rules: tuple[str, ...]) -> dict[str, Any]:
    """构建最终返回给客户端的配置对象。"""

    proxy_names = [proxy["name"] for proxy in proxies]

    # 纯物理节点列表，供 url-test 和 fallback 使用。
    pure_nodes = proxy_names

    # 包含所有前置选项和物理节点的完整列表，供大多数策略组使用。
    full_proxies = ["节点选择", "自动选择", "全球直连", "故障转移"] + proxy_names

    # Microsoft 和 Apple 默认选择直连，仍可手动切换代理。
    direct_first_proxies = ["全球直连", "节点选择", "自动选择", "故障转移"] + proxy_names

    proxy_groups = [
        {"name": "节点选择", "type": "select", "proxies": ["自动选择", "全球直连", "故障转移"] + proxy_names},
        {"name": "自动选择", "type": "url-test", "proxies": pure_nodes, "url": "http://cp.cloudflare.com/generate_204", "interval": 300},
        {"name": "故障转移", "type": "fallback", "proxies": pure_nodes, "url": "http://cp.cloudflare.com/generate_204", "interval": 300},
        # 供其他分组和规则引用的固定直连入口。
        {"name": "全球直连", "type": "select", "proxies": ["DIRECT"]},
        {"name": "广告", "type": "select", "proxies": ["REJECT", "DIRECT", "节点选择"]},
        {"name": "漏网之鱼", "type": "select", "proxies": full_proxies},
        {"name": "NETFLIX", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/netflix.svg"},
        {"name": "HBO", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/hbo.svg"},
        {"name": "YouTube", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/youtube.svg"},
        {"name": "Disney", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/disney-plus.svg"},
        {"name": "巴哈姆特", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/bahamut.svg"},
        {"name": "OpenAI", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/chatgpt.svg"},
        {"name": "Anthropic", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/claude.svg"},
        {"name": "Grok", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/grok.svg"},
        {"name": "IP 检测", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/ip-check.svg"},
        {"name": "Telegram", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/telegram.svg"},
        {"name": "X", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/x.svg"},
        {"name": "Google", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/google.svg"},
        {"name": "Github", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/github.svg"},
        {"name": "Tiktok", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/tiktok.svg"},
        {"name": "Playstation", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/playstation.svg"},
        {"name": "Xbox", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/xbox.svg"},
        {"name": "Bilibili 海外", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/bilibili.svg"},
        {"name": "Steam", "type": "select", "proxies": full_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/steam.svg"},
        {"name": "Microsoft", "type": "select", "proxies": direct_first_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/microsoft.svg"},
        {"name": "Apple", "type": "select", "proxies": direct_first_proxies, "icon": "https://cdn.jsdelivr.net/gh/happycola233/assets@v1.4/group-icons/apple.svg"},
        {"name": "备用代理组1", "type": "select", "proxies": full_proxies},
        {"name": "备用代理组2", "type": "select", "proxies": full_proxies},
        {"name": "备用代理组3", "type": "select", "proxies": full_proxies},
        {"name": "备用代理组4", "type": "select", "proxies": full_proxies},
        {"name": "备用代理组5", "type": "select", "proxies": full_proxies},
    ]

    return {
        "port": 7890,
        "socks-port": 7891,
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "ipv6": False,
        "find-process-mode": "strict",
        # 服务 IP 分组使用 geoip.dat；数据库下载与更新交给客户端内核。
        "geodata-mode": True,
        "geo-auto-update": True,
        "geo-update-interval": 24,
        "geox-url": {
            "geoip": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geoip.dat",
            "geosite": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geosite.dat",
            "mmdb": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geoip.metadb",
            "asn": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/GeoLite2-ASN.mmdb",
        },
        "external-controller": "127.0.0.1:9090",
        "tun": {
            "enable": True,
            "stack": "mixed",
            "device": "Mihomo",
            "auto-route": True,
            "strict-route": True,
            "auto-detect-interface": True,
            "dns-hijack": ["any:53"],
            "mtu": 1500,
            "inet4-route-exclude-address": ["192.168.0.0/16"],
        },
        "dns": {
            "enable": True,
            "listen": "0.0.0.0:1053",
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "fake-ip-filter": [
                "+.lan",
                "+.local",
                "+.msftconnecttest.com",
                "+.msftncsi.com",
                "time.*.com",
                "time.*.gov",
                "time.*.apple.com",
                "ntp.*.com",
                "+.pool.ntp.org",
                "stun.*.*",
                "stun.*.*.*",
                "+.*.stun.playstation.net",
                "+.*.srv.nintendo.net",
                "xbox.*.*.microsoft.com",
                "+.*.xboxlive.com",
                "+.wargaming.net",
                "+.music.163.com",
                "+.126.net",
                "+.y.qq.com",
                "+.music.tc.qq.com",
                "+.stream.qqmusic.qq.com",
                "api.bilibili.com",
                "+.kuwo.cn",
                "+.kugou.com",
            ],
            "default-nameserver": ["223.5.5.5", "119.29.29.29", "114.114.114.114"],
            "proxy-server-nameserver": ["https://dns.alidns.com/dns-query", "https://doh.pub/dns-query"],
            "nameserver": ["https://dns.alidns.com/dns-query", "https://doh.pub/dns-query"],
            "fallback": ["tls://8.8.4.4:853", "https://cloudflare-dns.com/dns-query", "119.29.29.29"],
            "fallback-filter": {"geoip": True, "geoip-code": "CN", "ipcidr": ["240.0.0.0/4"]},
        },
        "proxies": proxies,
        "proxy-groups": proxy_groups,
        "rule-providers": {
            "ads": {"type": "http", "behavior": "domain", "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/reject.txt", "path": "./ruleset/ads.yaml", "interval": 86400},
            "proxy": {"type": "http", "behavior": "domain", "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/proxy.txt", "path": "./ruleset/proxy.yaml", "interval": 86400},
            "direct": {"type": "http", "behavior": "domain", "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/direct.txt", "path": "./ruleset/direct.yaml", "interval": 86400},
            "private": {"type": "http", "behavior": "domain", "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/private.txt", "path": "./ruleset/private.yaml", "interval": 86400},
            "gfw": {"type": "http", "behavior": "domain", "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/gfw.txt", "path": "./ruleset/gfw.yaml", "interval": 86400},
            "tld-not-cn": {"type": "http", "behavior": "domain", "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/tld-not-cn.txt", "path": "./ruleset/tld-not-cn.yaml", "interval": 86400},
            "cncidr": {"type": "http", "behavior": "ipcidr", "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/cncidr.txt", "path": "./ruleset/cncidr.yaml", "interval": 86400},
            "lancidr": {"type": "http", "behavior": "ipcidr", "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/lancidr.txt", "path": "./ruleset/lancidr.yaml", "interval": 86400},
            "applications": {"type": "http", "behavior": "classical", "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/applications.txt", "path": "./ruleset/applications.yaml", "interval": 86400},
        },
        "rules": [
            # 屏蔽 QUIC：静默丢弃发往 UDP 443 端口的流量。
            "AND,((NETWORK,UDP),(DST-PORT,443)),REJECT-DROP",
            # 广告先于服务规则匹配，避免被 Google、Microsoft 等主名单抢先命中。
            "RULE-SET,ads,广告",
            # BT/PT 下载进程先于服务、通用代理规则直连。
            r"PROCESS-NAME-REGEX,(?i)^(qbittorrent(-nox)?|transmission.*|deluge.*|aria2c?|motrix|utorrent|bitcomet(_x64)?)(\.exe)?$,DIRECT",
        ]
        + list(commercial_rules)
        + [
            "RULE-SET,applications,全球直连",
            "RULE-SET,private,全球直连",
            "RULE-SET,proxy,节点选择",
            "RULE-SET,gfw,节点选择",
            "RULE-SET,direct,全球直连",
            "RULE-SET,lancidr,全球直连",
            "RULE-SET,cncidr,全球直连",
            "GEOIP,LAN,全球直连",
            "GEOIP,CN,全球直连",
            "MATCH,漏网之鱼",
        ],
    }


def dump_yaml(config: dict[str, Any]) -> str:
    """渲染 YAML，并保留 emoji、中文等 Unicode 字符的原始显示形态。"""

    return yaml.dump(config, allow_unicode=True, sort_keys=False)
