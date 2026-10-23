from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests
import yaml

from clash_sub_server.app import create_app
from clash_sub_server.config_loader import ConfigManager, load_config
from clash_sub_server.errors import ConfigError
from clash_sub_server.models import ProxySettings
from clash_sub_server.rule_update_service import RuleUpdateService
from clash_sub_server.rule_updater import Upstream


class RuleUpdateServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "commercial_rules.list"
        self.old_rules = "DOMAIN,old.example,节点选择\n"
        self.output.write_text(self.old_rules, encoding="utf-8")
        self.manifest = self.root / "sources.yaml"
        self.manifest.write_text(
            "upstream:\n  repository: example/rules\n  ref: meta\n"
            "rulesets:\n  - policy: 节点选择\n"
            "    files: [geo/geosite/classical/example.list]\n", encoding="utf-8",
        )
        self.config_path = self.root / "config.yaml"
        self.config_data = {
            "hot_reload": {"enabled": False},
            "rules": {"commercial_rules_path": "commercial_rules.list"},
            "tokens": {"test-token": {"source": "nodes.yaml", "userinfo_mode": "source"}},
        }
        self.write_config()
        (self.root / "nodes.yaml").write_text(yaml.safe_dump({
            "proxies": [{"name": "test-node", "type": "ss", "server": "127.0.0.1", "port": 12345,
                         "cipher": "aes-128-gcm", "password": "test-only"}],
        }), encoding="utf-8")
        self.manager = ConfigManager(self.config_path)
        self.service = RuleUpdateService(self.manager)
        self.addCleanup(self.service.stop)
        self.upstream_patch = patch("clash_sub_server.rule_update_service.Upstream")
        self.factory = self.upstream_patch.start()
        self.addCleanup(self.upstream_patch.stop)
        self.upstream = self.factory.return_value
        self.upstream.resolve.return_value = "a" * 40
        self.upstream.read.return_value = "DOMAIN,new.example\n"

    def write_config(self):
        self.config_path.write_text(yaml.safe_dump(self.config_data, allow_unicode=True), encoding="utf-8")

    def test_old_configuration_enables_daily_updates_without_new_options(self):
        settings = self.manager.get_config().rules
        self.assertTrue(settings.auto_update)
        self.assertEqual(settings.update_interval, 86400)
        self.assertEqual(settings.sources_path, self.manifest)

    def test_success_immediately_updates_cache_even_when_hot_reload_is_disabled(self):
        old = self.manager.get_config()
        self.service.check_once()
        current = self.manager.get_config()
        self.assertEqual(old.commercial_rules, (self.old_rules.strip(),))
        self.assertEqual(current.commercial_rules, ("DOMAIN,new.example,节点选择",))
        self.assertIn(current.commercial_rules[0], self.output.read_text(encoding="utf-8"))

    def test_periodic_checks_do_not_download_again_before_the_interval(self):
        with patch("clash_sub_server.rule_update_service.time.monotonic", return_value=100):
            self.service.check_once()
        with patch("clash_sub_server.rule_update_service.time.monotonic", return_value=160):
            self.service.check_once()
        self.assertEqual(self.upstream.resolve.call_count, 1)
        with patch("clash_sub_server.rule_update_service.time.monotonic", return_value=86500):
            self.service.check_once()
        self.assertEqual(self.upstream.resolve.call_count, 2)

    def test_network_failure_keeps_old_file_and_cache_then_retries(self):
        self.manager.get_config()
        self.upstream.resolve.side_effect = requests.ConnectionError("offline")
        with patch("clash_sub_server.rule_update_service.time.monotonic", return_value=0), \
                self.assertLogs("clash_sub_server.rule_update_service", level="ERROR"):
            self.service.check_once()
        self.assertEqual(self.output.read_text(encoding="utf-8"), self.old_rules)
        self.assertEqual(self.manager.get_config().commercial_rules, (self.old_rules.strip(),))
        with patch("clash_sub_server.rule_update_service.time.monotonic", return_value=60):
            self.service.check_once()
        self.assertEqual(self.upstream.resolve.call_count, 1)
        self.upstream.resolve.side_effect = None
        with patch("clash_sub_server.rule_update_service.time.monotonic", return_value=3600):
            self.service.check_once()
        self.assertEqual(self.upstream.resolve.call_count, 2)
        self.assertEqual(self.manager.get_config().commercial_rules, ("DOMAIN,new.example,节点选择",))

    def test_disabled_updates_do_not_download(self):
        self.config_data["rules"]["auto_update"] = False
        self.write_config()
        self.service.check_once()
        self.factory.assert_not_called()
        self.assertEqual(self.output.read_text(encoding="utf-8"), self.old_rules)

    def test_local_source_changes_trigger_update_before_daily_deadline(self):
        local = self.root / "ip_check.list"
        local.write_text("DOMAIN,check.example\n", encoding="utf-8")
        self.manifest.write_text(self.manifest.read_text(encoding="utf-8") + "    local_files: [ip_check.list]\n",
                                 encoding="utf-8")
        with patch("clash_sub_server.rule_update_service.time.monotonic", return_value=0):
            self.service.check_once()
        local.write_text("DOMAIN,second-check.example\n", encoding="utf-8")
        with patch("clash_sub_server.rule_update_service.time.monotonic", return_value=60):
            self.service.check_once()
        self.assertEqual(self.upstream.resolve.call_count, 2)
        self.assertIn("DOMAIN,second-check.example,节点选择", self.manager.get_config().commercial_rules)

    def test_disabling_during_download_discards_pending_result(self):
        def disable(ref):
            self.config_data["rules"]["auto_update"] = False
            self.write_config()
            self.manager.reload_now()
            return "a" * 40

        self.upstream.resolve.side_effect = disable
        self.service.check_once()
        self.assertEqual(self.output.read_text(encoding="utf-8"), self.old_rules)
        self.assertEqual(self.manager.get_config().commercial_rules, (self.old_rules.strip(),))

    def test_source_edit_during_download_discards_stale_result(self):
        def edit(ref):
            self.manifest.write_text(self.manifest.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
            return "a" * 40

        self.upstream.resolve.side_effect = edit
        self.service.check_once()
        self.assertEqual(self.output.read_text(encoding="utf-8"), self.old_rules)

    def test_updater_reuses_existing_proxy_and_bypass_settings(self):
        settings = ProxySettings(enabled=True, http="http://127.0.0.1:7890", https="http://127.0.0.1:7891",
                                 no_proxy=("api.github.com",))
        upstream = Upstream("example/rules", proxy_settings=settings)
        with patch("clash_sub_server.rule_updater.requests.get") as get:
            upstream._get("https://raw.githubusercontent.com/example/rules/main/rules.list")
            self.assertEqual(get.call_args.kwargs["proxies"], {
                "http": settings.http, "https": settings.https,
            })
            upstream._get("https://api.github.com/repos/example/rules/commits/main")
            self.assertIsNone(get.call_args.kwargs["proxies"])

    def test_invalid_update_intervals_are_rejected(self):
        for value in [0, -1, True, "daily"]:
            with self.subTest(value=value):
                self.config_data["rules"]["update_interval"] = value
                self.write_config()
                with self.assertRaises(ConfigError):
                    load_config(self.config_path)

    def test_app_serves_existing_subscription_while_startup_update_is_blocked(self):
        entered = threading.Event()
        release = threading.Event()

        def resolve(ref):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test download was not released")
            return "a" * 40

        self.upstream.resolve.side_effect = resolve
        app = create_app(self.manager)
        worker = app.extensions["rule_update_service"]
        try:
            self.assertTrue(entered.wait(2))
            thread = worker._thread
            worker.start()
            self.assertIs(worker._thread, thread)
            with app.test_client() as client:
                response = client.get("/c-sub?token=test-token")
            self.assertEqual(response.status_code, 200)
            rules = yaml.safe_load(response.get_data(as_text=True))["rules"]
            self.assertIn(self.old_rules.strip(), rules)
            self.assertNotIn("DOMAIN,new.example,节点选择", rules)
        finally:
            release.set()
            worker.stop()
