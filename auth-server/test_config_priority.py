# -*- coding: utf-8 -*-
"""
配置优先级回归测试
==================

锁定约定：网页后台 state > 环境变量(.env / compose) > config.json > 默认值。

这个约定曾被打碎过一次：compose 给 UNILINK_APP_CLIENT_ID 等可选项设了非空
默认值，而 load() 里又是「环境变量优先」，两者叠加导致面板里改的值永远不生效
（用户在 /admin 改了 client_id，/api/app/config 仍返回旧值）。这里把优先级
钉死，防止再次反转。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfgmod


class ConfigPriorityTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self._orig = (cfgmod.STATE_DIR, cfgmod.STATE_PATH)
        cfgmod.STATE_DIR = d
        cfgmod.STATE_PATH = os.path.join(d, "state.json")
        # 清掉同进程里可能残留的 UNILINK_* 环境变量
        self._env = {k: v for k, v in os.environ.items() if k.startswith("UNILINK_")}
        for k in self._env:
            del os.environ[k]

    def tearDown(self):
        cfgmod.STATE_DIR, cfgmod.STATE_PATH = self._orig
        os.environ.update(self._env)
        self.tmp.cleanup()

    def test_panel_overrides_env(self):
        """面板写过的值必须压过环境变量（这就是当年失效的那条路径）"""
        os.environ["UNILINK_APP_CLIENT_ID"] = "from-env"
        os.environ["UNILINK_BASE_URL"] = "https://env.test"
        os.environ["UNILINK_AUTHENTIK_URL"] = "https://auth-env.test"
        os.environ["UNILINK_CLIENT_ID"] = "env-qr"
        os.environ["UNILINK_CLIENT_SECRET"] = "e" * 40
        cfgmod.save_state({
            "base_url": "https://panel.test",
            "authentik_url": "https://auth-panel.test",
            "client_id": "panel-qr",
            "client_secret": "p" * 40,
            "app_client_id": "from-panel",
        })
        cfg = cfgmod.load()
        self.assertEqual(cfg.base_url, "https://panel.test")
        self.assertEqual(cfg.authentik_url, "https://auth-panel.test")
        self.assertEqual(cfg.app_client_id, "from-panel")
        # 客户端表也跟着面板的 client_id / secret 走，回调地址同步拼对
        self.assertEqual(list(cfg.clients), ["panel-qr"])
        c = cfg.clients["panel-qr"]
        self.assertEqual(c.client_secret, "p" * 40)
        self.assertEqual(c.redirect_uris,
                         ("https://auth-panel.test/source/oauth/callback/panel-qr/",))

    def test_env_fills_gap_when_panel_silent(self):
        """面板没写的项回落到环境变量（留空 = 不改，不是清空）"""
        os.environ["UNILINK_AUTHENTIK_URL"] = "https://auth-fallback.test"
        os.environ["UNILINK_CLIENT_ID"] = "env-qr"
        os.environ["UNILINK_CLIENT_SECRET"] = "e" * 40
        os.environ["UNILINK_APP_CLIENT_ID"] = "from-env"
        cfgmod.save_state({"base_url": "https://panel2.test"})
        cfg = cfgmod.load()
        self.assertEqual(cfg.authentik_url, "https://auth-fallback.test")
        self.assertEqual(cfg.app_client_id, "from-env")
        self.assertEqual(list(cfg.clients), ["env-qr"])

    def test_bad_int_still_raises(self):
        """整数项校验不能因为取值来源变了而失效"""
        cfgmod.save_state({
            "base_url": "https://panel.test",
            "authentik_url": "https://auth-panel.test",
            "client_id": "panel-qr",
            "client_secret": "p" * 40,
        })
        os.environ["UNILINK_MAX_SESSIONS"] = "not-a-number"
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.load()

    def test_unconfigured_enters_setup_mode(self):
        """必填项缺失时 load_or_none() 返回 None，进入首次配置模式"""
        os.environ["UNILINK_CLIENT_ID"] = "env-qr"
        os.environ["UNILINK_CLIENT_SECRET"] = "e" * 40
        cfgmod.save_state({})          # 一张空 state
        self.assertIsNone(cfgmod.load_or_none())


if __name__ == "__main__":
    unittest.main()
