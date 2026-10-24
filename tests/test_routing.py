from pathlib import Path
import re
import unittest

import yaml

from clash_sub_server.config_loader import load_config
from clash_sub_server.renderer import build_final_config, dump_yaml


ROOT = Path(__file__).resolve().parent.parent


class RoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_config(ROOT / "config.example.yaml").commercial_rules
        cls.config = build_final_config([{"name": "test-node", "type": "direct"}], cls.rules)

    def test_specific_services_win_over_parent_lists(self):
        positions = {rule.split(",")[1]: i for i, rule in enumerate(self.rules) if rule.startswith("GEOSITE,")}
        for child, parent in [("youtube", "google"), ("github", "microsoft"),
                              ("xbox", "microsoft"), ("xai", "twitter")]:
            with self.subTest(child=child, parent=parent):
                self.assertLess(positions[child], positions[parent])

    def test_selected_subsets_and_ai_ownership_are_preserved(self):
        by_policy = {}
        for rule in self.rules:
            kind, value, policy, *_ = rule.split(",")
            if kind == "GEOSITE":
                by_policy.setdefault(policy, set()).add(value)
        self.assertEqual(by_policy["Bilibili 海外"], {"biliintl", "bilibili@!cn"})
        self.assertEqual(by_policy["Google"], {"google"})
        self.assertEqual(by_policy["Github"], {"github"})
        self.assertEqual(by_policy["Microsoft"], {"microsoft"})
        self.assertEqual(by_policy["Apple"], {"apple", "apple-intelligence"})
        self.assertFalse(any("category-ai" in rule or "bilibili2" in rule for rule in self.rules))

    def test_client_ip_detection_preserves_exactly_the_original_nine_rules(self):
        actual = [rule for rule in self.rules if rule.endswith(",IP 检测")]
        expected = {f"DOMAIN-SUFFIX,{domain},IP 检测" for domain in [
            "ip.sb", "ipapi.co", "api.ipapi.is", "ipwho.is", "ip.api.skk.moe",
            "api.myip.com", "ident.me", "ip-api.com", "ipinfo.io",
        ]}
        self.assertEqual(len(actual), 9)
        self.assertEqual(set(actual), expected)

    def test_ip_groups_follow_domains_without_forcing_dns_resolution(self):
        ip_rules = [rule for rule in self.rules if rule.startswith("GEOIP,")]
        self.assertEqual(set(ip_rules), {
            "GEOIP,netflix,NETFLIX,no-resolve",
            "GEOIP,telegram,Telegram,no-resolve",
            "GEOIP,twitter,X,no-resolve",
        })
        self.assertEqual(list(self.rules[-len(ip_rules):]), ip_rules)
        self.assertTrue(self.config["geodata-mode"])
        self.assertFalse(any(rule.startswith("IP-CIDR") for rule in self.rules))

    def test_bt_direct_rules_precede_services_and_fallbacks(self):
        rules = self.config["rules"]
        process_rules = [rule for rule in rules if rule.startswith("PROCESS-NAME-REGEX,")]
        self.assertEqual(len(process_rules), 1)
        bt_rules = ["GEOSITE,category-public-tracker,DIRECT", "GEOSITE,category-pt,DIRECT", *process_rules]
        for rule in bt_rules:
            self.assertTrue(rule.endswith(",DIRECT"))
            self.assertLess(rules.index(rule), rules.index(self.rules[0]))
            self.assertLess(rules.index(rule), rules.index("RULE-SET,proxy,节点选择"))
            self.assertLess(rules.index(rule), rules.index("MATCH,漏网之鱼"))

        # 从实际返回的 YAML 读取正则，防止反斜杠在序列化时改变含义。
        rendered = yaml.safe_load(dump_yaml(self.config))
        pattern = next(rule for rule in rendered["rules"] if rule.startswith("PROCESS-NAME-REGEX,")).split(",")[1]
        for name in ["qBittorrent.exe", "qbittorrent-nox", "transmission-daemon", "transmission-qt.exe",
                     "deluged", "deluge.exe", "aria2", "aria2c.exe", "Motrix.exe", "uTorrent.exe",
                     "BitComet.exe", "BitComet_x64.exe"]:
            with self.subTest(process=name):
                self.assertIsNotNone(re.fullmatch(pattern, name))
        for name in ["chrome.exe", "steam.exe", "qbittorrent-helper", "myqbittorrent.exe",
                     "qbittorrentXexe", "qbittorrent.exe.bak"]:
            with self.subTest(process=name):
                self.assertIsNone(re.fullmatch(pattern, name))

    def test_ads_precede_services_and_replaced_providers_are_removed(self):
        rules = self.config["rules"]
        self.assertLess(rules.index("RULE-SET,ads,广告"), rules.index(self.rules[0]))
        for provider in ["telegramcidr", "icloud", "apple", "google"]:
            self.assertNotIn(provider, self.config["rule-providers"])
            self.assertFalse(any(rule.startswith(f"RULE-SET,{provider},") for rule in rules))

    def test_all_rule_targets_exist_and_service_defaults_match_preferences(self):
        groups = {group["name"]: group for group in self.config["proxy-groups"]}
        self.assertNotIn("其他国外 AI", groups)
        self.assertNotIn("DisneyPlus", groups)
        self.assertNotIn("哔哩哔哩", groups)
        for rule in self.rules:
            self.assertIn(rule.split(",")[2], groups)
        for name in ["Steam", "Xbox", "Bilibili 海外", "X"]:
            self.assertEqual(groups[name]["proxies"][0], "节点选择")
        for name in ["Microsoft", "Apple"]:
            self.assertEqual(groups[name]["proxies"][0], "全球直连")

    def test_client_receives_daily_geodata_update_settings(self):
        self.assertTrue(self.config["geo-auto-update"])
        self.assertEqual(self.config["geo-update-interval"], 24)
        for kind in ["geoip", "geosite"]:
            self.assertEqual(self.config["geox-url"][kind],
                             f"https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/{kind}.dat")
