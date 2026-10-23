from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from clash_sub_server.rule_updater import (
    Manifest, RuleSet, UpdateError, generate, load_manifest, main, parse_rules, replace_output,
)


class RuleUpdaterTests(unittest.TestCase):
    def test_duplicate_conditions_keep_first_policy_and_source_order(self):
        manifest = Manifest("example/rules", "meta", Path("."), (
            RuleSet("specific", "first", ("specific.list",), ()),
            RuleSet("general", "second", ("general.list",), ()),
        ))
        upstream = Mock()
        upstream.read.side_effect = lambda revision, path: {
            "specific.list": "DOMAIN,api.example.com\nDOMAIN,api.example.com\n",
            "general.list": "DOMAIN,api.example.com\nDOMAIN-SUFFIX,example.com\n",
        }[path]
        output, count, duplicates = generate(manifest, upstream, "a" * 40)
        rules = [line for line in output.splitlines() if line and not line.startswith("#")]
        self.assertEqual(rules, ["DOMAIN,api.example.com,first", "DOMAIN-SUFFIX,example.com,second"])
        self.assertEqual((count, duplicates), (2, 2))

    def test_ipv6_and_ip_options_are_normalized(self):
        source = "IP-CIDR,91.108.4.0/22,no-resolve\nIP-CIDR,2001:67c:4e8::/48\n"
        self.assertEqual(parse_rules(source, "ip.list"), [
            "IP-CIDR,91.108.4.0/22", "IP-CIDR6,2001:67c:4e8::/48",
        ])
        manifest = Manifest("example/rules", "meta", Path("."), (
            RuleSet("IP", "proxy", ("ip.list",), ()),
        ))
        output, _, _ = generate(manifest, Mock(read=Mock(return_value=source)), "a" * 40)
        self.assertIn("IP-CIDR6,2001:67c:4e8::/48,proxy,no-resolve", output)

    def test_empty_malformed_or_policy_bearing_sources_are_rejected(self):
        for text in ["# empty", "<html>404</html>", "DOMAIN,example.com,DIRECT", "IP-CIDR,1.2.3.4/99"]:
            with self.subTest(text=text), self.assertRaises(UpdateError):
                parse_rules(text, "bad.list")

    def test_failed_update_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "sources.yaml"
            manifest_path.write_text(
                "upstream:\n  repository: example/rules\n  ref: meta\n"
                "rulesets:\n  - policy: DIRECT\n"
                "    files: [geo/geosite/classical/example.list]\n", encoding="utf-8",
            )
            output = root / "rules.list"
            for result in [requests.ConnectionError("download failed"), "<html>bad response</html>"]:
                with self.subTest(result=result):
                    output.write_text("previous valid rules\n", encoding="utf-8")
                    with patch("clash_sub_server.rule_updater.Upstream") as factory, redirect_stdout(StringIO()):
                        factory.return_value.resolve.return_value = "a" * 40
                        if isinstance(result, Exception):
                            factory.return_value.read.side_effect = result
                        else:
                            factory.return_value.read.return_value = result
                        self.assertEqual(main(["--manifest", str(manifest_path), "--output", str(output)]), 2)
                    self.assertEqual(output.read_text(encoding="utf-8"), "previous valid rules\n")

    def test_check_reports_change_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "sources.yaml"
            manifest.write_text(
                "upstream:\n  repository: example/rules\n  ref: meta\n"
                "rulesets:\n  - policy: DIRECT\n"
                "    files: [geo/geosite/classical/example.list]\n", encoding="utf-8",
            )
            output = root / "rules.list"
            output.write_text("old\n", encoding="utf-8")
            with patch("clash_sub_server.rule_updater.Upstream") as factory, redirect_stdout(StringIO()):
                factory.return_value.resolve.return_value = "a" * 40
                factory.return_value.read.return_value = "DOMAIN,example.com"
                self.assertEqual(main(["--manifest", str(manifest), "--output", str(output), "--check"]), 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "old\n")

    def test_unchanged_output_does_not_trigger_hot_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "rules.list"
            self.assertTrue(replace_output(output, "content\n"))
            modified = output.stat().st_mtime_ns
            self.assertFalse(replace_output(output, "content\n"))
            self.assertEqual(output.stat().st_mtime_ns, modified)

    def test_unknown_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.yaml"
            path.write_text(
                "upstream:\n  repository: example/rules\n  ref: meta\n"
                "rulesets:\n  - policy: unknown-policy\n    local_files: [ip_check.list]\n", encoding="utf-8",
            )
            with self.assertRaisesRegex(UpdateError, "策略组不存在"):
                load_manifest(path)
