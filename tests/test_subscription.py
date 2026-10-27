from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from clash_sub_server.app import create_app
from clash_sub_server.config_loader import ConfigManager


ROOT = Path(__file__).resolve().parent.parent


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.rules_path = self.root / "commercial_rules.list"
        self.original_rules = (ROOT / "rules" / "commercial_rules.list").read_text(encoding="utf-8")
        self.rules_path.write_text(self.original_rules, encoding="utf-8")
        config = {
            "rules": {
                "commercial_rules_path": "commercial_rules.list",
                # 升级前的配置仍能加载；已移除的更新选项和不存在的来源文件不会触发下载。
                "auto_update": True, "update_interval": 86400, "sources_path": "missing-sources.yaml",
            },
            "tokens": {"test-token": {"source": "nodes.yaml", "userinfo_mode": "source"}},
        }
        config_path = self.root / "config.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        (self.root / "nodes.yaml").write_text(yaml.safe_dump({
            "proxies": [{"name": "test-node", "type": "ss", "server": "127.0.0.1", "port": 12345,
                         "cipher": "aes-128-gcm", "password": "test-only"}],
        }), encoding="utf-8")
        self.manager = ConfigManager(config_path)

    def test_startup_and_local_subscription_need_no_rule_download_or_background_worker(self):
        with patch("threading.Thread.start", side_effect=AssertionError("unexpected startup worker")):
            app = create_app(self.manager)
        with patch("requests.get", side_effect=AssertionError("unexpected download")) as get:
            with app.test_client() as client:
                response = client.get("/c-sub?token=test-token")
            get.assert_not_called()
        self.assertEqual(response.status_code, 200)
        rendered = yaml.safe_load(response.get_data(as_text=True))
        self.assertIn("GEOSITE,netflix,NETFLIX", rendered["rules"])
        self.assertTrue(rendered["geo-auto-update"])
        self.assertEqual(self.rules_path.read_text(encoding="utf-8"), self.original_rules)

    def test_local_rule_mapping_changes_hot_reload_and_invalid_files_keep_previous_rules(self):
        app = create_app(self.manager)
        added_rule = "DOMAIN,local-override.example,节点选择"
        with app.test_client() as client:
            self.assertEqual(client.get("/c-sub?token=test-token").status_code, 200)
            self.rules_path.write_text(self.original_rules + added_rule + "\n", encoding="utf-8")
            updated = client.get("/c-sub?token=test-token")
            self.assertEqual(updated.status_code, 200)
            self.assertIn(added_rule, yaml.safe_load(updated.get_data(as_text=True))["rules"])
            self.rules_path.write_text("# no rules\n", encoding="utf-8")
            with self.assertLogs("clash_sub_server.config_loader", level="ERROR"):
                fallback = client.get("/c-sub?token=test-token")
            self.assertEqual(fallback.status_code, 200)
            self.assertEqual(yaml.safe_load(fallback.get_data(as_text=True)), yaml.safe_load(updated.get_data(as_text=True)))
