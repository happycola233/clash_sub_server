from pathlib import Path
import re
import unittest

from clash_sub_server.config_loader import load_config
from clash_sub_server.renderer import build_final_config


ROOT = Path(__file__).resolve().parent.parent


class RoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_config(ROOT / "config.example.yaml").commercial_rules
        cls.config = build_final_config([{"name": "test-node", "type": "direct"}], cls.rules)

    def first_domain_policy(self, domain):
        for line in self.rules:
            kind, value, policy, *_ = line.split(",")
            if kind == "DOMAIN" and domain == value:
                return policy
            if kind == "DOMAIN-SUFFIX" and (domain == value or domain.endswith("." + value)):
                return policy
            if kind == "DOMAIN-KEYWORD" and value in domain:
                return policy
            if kind == "DOMAIN-REGEX" and re.search(value, domain):
                return policy
        return None

    def test_specific_services_win_over_parent_lists(self):
        cases = {
            "www.youtube.com": "YouTube",
            "gemini.google.com": "Google",
            "aistudio.google.com": "Google",
            "notebooklm.google.com": "Google",
            "github.com": "Github",
            "github.dev": "Github",
            "api.githubcopilot.com": "Github",
            "copilot.com": "Microsoft",
            "copilot.microsoft.com": "Microsoft",
            "copilot.cloud.microsoft": "Microsoft",
            "chatgpt.com": "OpenAI",
            "claude.ai": "Anthropic",
            "grok.com": "Grok",
            "grok.x.com": "Grok",
            "x.com": "X",
            "api.twitter.com": "X",
            "t.co": "X",
            "pbs.twimg.com": "X",
            "cursor.com": None,
            "perplexity.ai": None,
            "xboxlive.cn": "Xbox",
            "store.steamchina.com": "Steam",
            "apple-relay.cloudflare.com": "Apple",
            "apple-relay.fastly-edge.com": "Apple",
            "tv.apple.com": "Apple",
            "www.bilibili.tv": "Bilibili 海外",
        }
        for domain, policy in cases.items():
            with self.subTest(domain=domain):
                self.assertEqual(self.first_domain_policy(domain), policy)

    def test_bilibili_domestic_and_unrelated_top_level_domains_are_not_claimed(self):
        for domain in ["www.bilibili.com", "bili2.cc", "unrelated-example.dev", "unrelated-example.app"]:
            with self.subTest(domain=domain):
                self.assertIsNone(self.first_domain_policy(domain))

    def test_client_ip_detection_preserves_exactly_the_original_nine_rules(self):
        actual = {rule for rule in self.rules if rule.endswith(",IP 检测")}
        expected = {f"DOMAIN-SUFFIX,{domain},IP 检测" for domain in [
            "ip.sb", "ipapi.co", "api.ipapi.is", "ipwho.is", "ip.api.skk.moe",
            "api.myip.com", "ident.me", "ip-api.com", "ipinfo.io",
        ]}
        self.assertEqual(actual, expected)

    def test_ip_rules_follow_domains_and_use_only_the_selected_groups(self):
        ip_rules = [rule for rule in self.rules if rule.startswith(("IP-CIDR,", "IP-CIDR6,"))]
        self.assertTrue(ip_rules)
        self.assertEqual({rule.split(",")[2] for rule in ip_rules}, {"Telegram", "NETFLIX", "X"})
        self.assertTrue(all(rule.endswith(",no-resolve") for rule in ip_rules))
        first_ip = next(i for i, rule in enumerate(self.rules) if rule.startswith("IP-CIDR"))
        self.assertTrue(all(rule.startswith("IP-CIDR") for rule in self.rules[first_ip:]))
        self.assertTrue(any(rule.startswith("IP-CIDR6,") for rule in ip_rules))

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
        conditions = [tuple(rule.split(",")[:2]) for rule in self.rules]
        self.assertEqual(len(conditions), len(set(conditions)))
