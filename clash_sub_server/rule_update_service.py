"""随订阅服务运行的规则更新线程，不占用订阅请求线程。"""

from __future__ import annotations

import logging
from pathlib import Path
import threading
import time

from .config_loader import ConfigManager
from .models import AppConfig
from .rule_updater import Manifest, Upstream, generate, load_manifest


logger = logging.getLogger(__name__)
POLL_INTERVAL = 60
FAILURE_RETRY_INTERVAL = 3600


def _input_signature(config: AppConfig, manifest: Manifest, path: Path) -> tuple:
    paths = {path}
    paths.update(manifest.directory / item for group in manifest.rulesets for item in group.local_files)
    files = []
    for item in sorted(paths):
        stat = item.stat()
        files.append((str(item), stat.st_mtime_ns, stat.st_size))
    return config.rules, config.proxy, tuple(files)


class RuleUpdateService:
    def __init__(self, manager: ConfigManager):
        self.manager = manager
        self._stop = threading.Event()
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._signature: tuple | None = None
        self._next_update = 0.0

    def start(self) -> None:
        with self._start_lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="commercial-rule-updater", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                delay = self.check_once()
            except Exception:  # 后台任务不能因配置或网络异常永久退出。
                logger.exception("规则自动更新检查失败，继续使用现有规则")
                delay = POLL_INTERVAL
            self._stop.wait(delay)

    def check_once(self) -> float:
        """到期或本地来源改变时更新；返回下一次轻量检查前的等待秒数。"""

        config = self.manager.get_config()
        settings = config.rules
        if not settings.auto_update:
            self._signature = None
            self._next_update = 0.0
            return POLL_INTERVAL

        path = settings.sources_path or settings.commercial_rules_path.with_name("sources.yaml")
        manifest = load_manifest(path)
        signature = _input_signature(config, manifest, path)
        if signature != self._signature:
            self._signature = signature
            self._next_update = 0.0

        now = time.monotonic()
        if now < self._next_update:
            return min(POLL_INTERVAL, self._next_update - now)

        try:
            upstream = Upstream(manifest.repository, proxy_settings=config.proxy)
            revision = upstream.resolve(manifest.ref)
            text, count, duplicates = generate(manifest, upstream, revision)
            if self._stop.is_set():
                return POLL_INTERVAL
            if _input_signature(config, manifest, path) != signature:
                # 下载期间有人修改了来源，下次使用新清单重试。
                return 0
            changed = self.manager.install_commercial_rules(settings, text)
            if changed is None:
                return 0
            logger.info(
                "商业规则自动更新完成: changed=%s rules=%s duplicates=%s upstream=%s",
                changed, count, duplicates, revision,
            )
            interval = settings.update_interval
        except Exception:  # 下载、解析或写入失败，保留正在服务的规则并稍后重试。
            interval = min(FAILURE_RETRY_INTERVAL, settings.update_interval)
            logger.exception("商业规则自动更新失败，继续使用现有规则，%s 秒后重试", interval)

        self._next_update = time.monotonic() + interval
        return min(POLL_INTERVAL, interval)
