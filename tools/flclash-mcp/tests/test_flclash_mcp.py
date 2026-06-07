#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import importlib.machinery
import json
import socket
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "flclash-mcp"


def load_module():
    spec = importlib.util.spec_from_file_location("flclash_mcp", MODULE_PATH, loader=importlib.machinery.SourceFileLoader("flclash_mcp", str(MODULE_PATH)))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FlClashMcpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.app_dir = self.base / "com.follow.clash"
        self.profiles_dir = self.app_dir / "profiles"
        self.profiles_dir.mkdir(parents=True)
        self.db = self.app_dir / "database.sqlite"
        con = sqlite3.connect(self.db)
        con.execute(
            'create table profiles (id integer primary key, label text, current_group_name text, url text, '
            'last_update_date integer, overwrite_type text, script_id integer, auto_update_duration_millis integer, '
            'subscription_info text, auto_update integer, selected_map text, unfold_set text, "order" integer)'
        )
        con.execute(
            'insert into profiles values (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (
                123,
                "demo",
                None,
                "https://example.test/demo.yaml",
                1700000000,
                "none",
                None,
                0,
                None,
                1,
                "{}",
                "[]",
                0,
            ),
        )
        con.commit()
        con.close()
        (self.profiles_dir / "123.yaml").write_text(
            yaml.safe_dump(
                {
                    "mixed-port": 7890,
                    "proxies": [
                        {"name": "ok", "type": "ss", "server": "127.0.0.1", "port": 8388, "cipher": "aes-128-gcm", "password": "p"},
                        {"name": "HK node", "type": "ss", "server": "127.0.0.1", "port": 8389, "cipher": "aes-128-gcm", "password": "p"},
                        {"name": "bad", "type": "hysteria2", "server": None, "port": -1, "password": ""},
                    ],
                    "proxy-groups": [{"name": "select", "type": "select", "proxies": ["ok", "HK node", "bad"]}],
                    "rules": ["MATCH,select"],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_profiles_reads_sqlite_and_counts_yaml(self):
        mcp = load_module()
        client = mcp.FlClashClient(app_dir=self.app_dir)
        profiles = client.list_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["id"], 123)
        self.assertEqual(profiles[0]["label"], "demo")
        self.assertEqual(profiles[0]["profile"]["proxies"], 3)

    def test_validate_profile_identifies_bad_proxy_and_group_reference(self):
        mcp = load_module()
        client = mcp.FlClashClient(app_dir=self.app_dir)
        report = client.validate_profile(123)
        self.assertEqual(report["proxies"], 3)
        self.assertEqual(report["badProxyCount"], 1)
        self.assertEqual(report["badProxies"][0]["name"], "bad")
        self.assertEqual(report["badReferenceCount"], 0)

    def test_delay_test_excludes_hk_by_default(self):
        mcp = load_module()
        client = mcp.FlClashClient(app_dir=self.app_dir)
        report = client.test_profile_delays(123, limit=10, timeout_ms=50)
        self.assertEqual(report["totalProxies"], 3)
        self.assertEqual(report["excluded"], 1)
        self.assertIn("HK", report["excludeKeywords"])
        names = [row["name"] for row in report["results"]]
        self.assertNotIn("HK node", names)

    def test_delay_test_can_override_exclude_keywords(self):
        mcp = load_module()
        client = mcp.FlClashClient(app_dir=self.app_dir)
        report = client.test_profile_delays(123, limit=10, timeout_ms=50, exclude_keywords=[])
        self.assertEqual(report["excluded"], 0)
        names = [row["name"] for row in report["results"]]
        self.assertIn("HK node", names)

    def test_tcp_delay_sets_delay_ms_when_reachable(self):
        mcp = load_module()
        client = mcp.FlClashClient(app_dir=self.app_dir)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def accept_once():
            conn, _ = listener.accept()
            conn.close()
            listener.close()

        thread = threading.Thread(target=accept_once, daemon=True)
        thread.start()
        result = client._tcp_delay({"name": "local", "type": "ss", "server": "127.0.0.1", "port": port}, 500)
        thread.join(timeout=1)
        self.assertTrue(result["reachable"])
        self.assertIn("delayMs", result)
        self.assertGreaterEqual(result["delayMs"], 0)

    def test_mcp_tools_list_contains_flclash_tools(self):
        mcp = load_module()
        response = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("list_profiles", names)
        self.assertIn("validate_profile", names)
        self.assertIn("test_profile_delays", names)


if __name__ == "__main__":
    unittest.main()
