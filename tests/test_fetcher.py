import unittest
from unittest.mock import PropertyMock, patch

from requests import Response
from requests.utils import get_encoding_from_headers
import yaml

from clash_sub_server.errors import SourceError
from clash_sub_server.fetcher import _load_remote_source
from clash_sub_server.models import ProxySettings, SourceRequestSettings


class RemoteSourceEncodingTests(unittest.TestCase):
    def setUp(self):
        self.proxies = [{
            "name": "日本节点", "type": "ss", "server": "example.test", "port": 443,
            "cipher": "aes-128-gcm", "password": "test-only",
        }]
        self.source_text = yaml.safe_dump({"proxies": self.proxies}, allow_unicode=True)
        self.userinfo = "upload=0; download=1; total=1024; expire=253402185600"

    def _load_response(self, content, content_type):
        response = Response()
        response.status_code = 200
        response._content = content
        response.headers["Subscription-Userinfo"] = self.userinfo
        if content_type is not None:
            response.headers["Content-Type"] = content_type
        response.encoding = get_encoding_from_headers(response.headers)
        with patch("clash_sub_server.fetcher.requests.get", return_value=response):
            return _load_remote_source("https://example.test/sub", SourceRequestSettings(), ProxySettings())

    def test_utf8_without_charset_preserves_unicode_node_names(self):
        for content_type in ["text/yaml", "application/yaml", None]:
            with self.subTest(content_type=content_type):
                result = self._load_response(self.source_text.encode("utf-8"), content_type)
                self.assertEqual(result.source_config["proxies"], self.proxies)
                self.assertEqual(result.remote_userinfo, self.userinfo)

    def test_utf8_bom_without_charset_is_supported(self):
        result = self._load_response(self.source_text.encode("utf-8-sig"), "text/yaml")
        self.assertEqual(result.source_config["proxies"], self.proxies)

    def test_explicit_charset_is_respected(self):
        for content_type, encoding in [
            ('text/yaml; Charset="gb18030"', "gb18030"),
            ("text/yaml; charset=utf-16", "utf-16"),
        ]:
            with self.subTest(content_type=content_type):
                result = self._load_response(self.source_text.encode(encoding), content_type)
                self.assertEqual(result.source_config["proxies"], self.proxies)

    def test_non_utf8_without_charset_uses_detected_encoding(self):
        with patch.object(Response, "apparent_encoding", new_callable=PropertyMock, return_value="gb18030"):
            result = self._load_response(self.source_text.encode("gb18030"), "text/yaml")
        self.assertEqual(result.source_config["proxies"], self.proxies)

    def test_invalid_declared_encoding_returns_source_error(self):
        for content_type, content in [
            ("text/yaml; charset=unknown-subscription-encoding", self.source_text.encode("utf-8")),
            ("text/yaml; charset=utf-8", b"proxies: [\xff]"),
        ]:
            with self.subTest(content_type=content_type), self.assertRaises(SourceError):
                self._load_response(content, content_type)

    def test_unrecognized_encoding_returns_source_error(self):
        with patch.object(Response, "apparent_encoding", new_callable=PropertyMock, return_value=None):
            with self.assertRaises(SourceError):
                self._load_response(b"proxies: [\xff]", "text/yaml")
