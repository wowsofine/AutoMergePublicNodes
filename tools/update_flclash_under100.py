#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml


REPO_PUBLIC_BASE = "https://raw.githubusercontent.com/wowsofine/AutoMergePublicNodes/master"
DEFAULT_OUTPUT = Path("flclash-sgjp-under100.yaml")
DEFAULT_LABEL = "SG-JP-TW-100ms"
DEFAULT_REGIONS = ("SG", "JP", "TW")
DEFAULT_THRESHOLD_MS = 100
FLCLASH_ID_EPOCH_MS = 1_704_067_200_000

REGION_KEYWORDS = {
    "SG": ("sg", "singapore", "新加坡", "狮城", "🇸🇬"),
    "JP": ("jp", "japan", "日本", "东京", "大阪", "🇯🇵"),
    "TW": ("tw", "taiwan", "台湾", "台灣", "中华民国", "中華民國", "🇹🇼"),
}
EXCLUDE_KEYWORDS = ("hk", "hong kong", "hkg", "香港", "🇭🇰")
CONFLICTING_REGION_KEYWORDS = {
    "SG": ("jp", "japan", "日本", "东京", "大阪", "tw", "taiwan", "台湾", "台灣", "us", "usa", "美国", "美國", "🇯🇵", "🇹🇼", "🇺🇸"),
    "JP": ("sg", "singapore", "新加坡", "狮城", "tw", "taiwan", "台湾", "台灣", "us", "usa", "美国", "美國", "🇸🇬", "🇹🇼", "🇺🇸"),
    "TW": ("sg", "singapore", "新加坡", "狮城", "jp", "japan", "日本", "东京", "大阪", "us", "usa", "美国", "美國", "🇸🇬", "🇯🇵", "🇺🇸"),
}

STATIC_SOURCES = (
    "https://raw.githubusercontent.com/xiaoji235/airport-free/main/clash/clashnodecc.txt",
    "https://raw.githubusercontent.com/xiaoji235/airport-free/main/clash/naidounode.txt",
    "https://raw.githubusercontent.com/xiaoji235/airport-free/main/clash/v2rayshare.txt",
    "https://raw.githubusercontent.com/Au1rxx/free-vpn-subscriptions/main/output/clash.yaml",
    "https://raw.githubusercontent.com/Au1rxx/free-vpn-subscriptions/main/output/by-country/clash-JP.yaml",
    "https://raw.githubusercontent.com/chengaopan/AutoMergePublicNodes/master/list.meta.yml",
    "https://raw.githubusercontent.com/wowsofine/AutoMergePublicNodes/master/list.meta.yml",
)


def daily_sources(now: datetime) -> list[str]:
    urls: list[str] = []
    for day in (now, now - timedelta(days=1)):
        ymd = day.strftime("%Y%m%d")
        ym = day.strftime("%Y/%m")
        for index in range(5):
            urls.append(f"https://node.nodeshare.net/uploads/{ym}/{index}-{ymd}.yaml")
        urls.append(f"https://raw.githubusercontent.com/free-nodes/clashfree/main/clash{ymd}.yml")
    return urls


def fetch_text(url: str, timeout: float) -> tuple[str, str | None, str | None]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 FlClash-Updater/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return url, response.read(8_000_000).decode("utf-8", "ignore"), None
    except Exception as exc:
        return url, None, f"{type(exc).__name__}: {str(exc)[:160]}"


def parse_clash_proxies(text: str) -> list[dict[str, Any]]:
    try:
        data = yaml.safe_load(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    proxies = data.get("proxies")
    if not isinstance(proxies, list):
        return []
    return [dict(item) for item in proxies if isinstance(item, dict)]


def classify_region(proxy: dict[str, Any], regions: set[str]) -> str | None:
    name = str(proxy.get("name") or "").lower()
    haystack = " ".join(str(proxy.get(key) or "") for key in ("name", "server")).lower()
    if any(keyword in haystack for keyword in EXCLUDE_KEYWORDS):
        return None
    for region in regions:
        if any(keyword in haystack for keyword in REGION_KEYWORDS[region]):
            if any(keyword in name for keyword in CONFLICTING_REGION_KEYWORDS[region]):
                continue
            return region
    return None


def tcp_delay_ms(proxy: dict[str, Any], timeout_ms: int) -> int | None:
    server = str(proxy.get("server") or "")
    try:
        port = int(proxy.get("port") or 0)
    except Exception:
        return None
    if not server or not 1 <= port <= 65535:
        return None
    started = time.perf_counter()
    try:
        with socket.create_connection((server, port), timeout=timeout_ms / 1000):
            return int((time.perf_counter() - started) * 1000)
    except Exception:
        return None


def unique_name(name: str, used: set[str]) -> str:
    candidate = name[:96]
    if candidate not in used:
        used.add(candidate)
        return candidate
    for index in range(2, 1000):
        suffix = f" #{index}"
        candidate = f"{name[:96 - len(suffix)]}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise RuntimeError("could not allocate unique proxy name")


def build_config(results: list[dict[str, Any]]) -> dict[str, Any]:
    used_names: set[str] = set()
    proxies: list[dict[str, Any]] = []
    for item in results:
        proxy = dict(item["proxy"])
        original_name = str(proxy.get("name") or "node")
        proxy["name"] = unique_name(f"{item['region']}-{item['delayMs']}ms-{original_name}", used_names)
        proxies.append(proxy)
    names = [proxy["name"] for proxy in proxies]
    return {
        "mixed-port": 7890,
        "allow-lan": True,
        "mode": "rule",
        "log-level": "info",
        "proxies": proxies,
        "proxy-groups": [
            {"name": "SG-JP-TW-UNDER100-SELECT", "type": "select", "proxies": list(names)},
            {
                "name": "SG-JP-TW-UNDER100-AUTO",
                "type": "url-test",
                "url": "https://www.gstatic.com/generate_204",
                "interval": 300,
                "tolerance": 50,
                "proxies": list(names),
            },
        ],
        "rules": ["MATCH,SG-JP-TW-UNDER100-SELECT"],
    }


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    proxies = [proxy for proxy in config.get("proxies") or [] if isinstance(proxy, dict)]
    names = {str(proxy.get("name")) for proxy in proxies}
    bad_proxies = [
        proxy.get("name")
        for proxy in proxies
        if not proxy.get("server") or not isinstance(proxy.get("port"), int) or not 1 <= proxy.get("port") <= 65535
    ]
    bad_refs: list[str] = []
    for group in config.get("proxy-groups") or []:
        if isinstance(group, dict):
            for ref in group.get("proxies") or []:
                if ref not in names:
                    bad_refs.append(str(ref))
    return {
        "proxies": len(proxies),
        "proxyGroups": len(config.get("proxy-groups") or []),
        "rules": len(config.get("rules") or []),
        "badProxyCount": len(bad_proxies),
        "badReferenceCount": len(bad_refs),
    }


def new_flclash_profile_id(con: sqlite3.Connection) -> int:
    timestamp_part = max(0, int(time.time() * 1000) - FLCLASH_ID_EPOCH_MS) << 22
    suffix_mask = (1 << 22) - 1
    for offset in range(256):
        profile_id = timestamp_part | ((time.time_ns() + offset) & suffix_mask)
        exists = con.execute("select 1 from profiles where id = ?", (profile_id,)).fetchone()
        if exists is None:
            return profile_id
    raise RuntimeError("could not allocate FlClash profile id")


def sync_flclash_profile(output: Path, label: str, public_url: str) -> dict[str, Any] | None:
    app_dir = Path.home() / "Library/Application Support/com.follow.clash"
    db_path = app_dir / "database.sqlite"
    profiles_dir = app_dir / "profiles"
    if not db_path.exists() or not profiles_dir.exists():
        return None
    text = output.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "select id from profiles where url = ? or label in (?, 'SG-JP-100ms') order by id desc limit 1",
            (public_url, label),
        ).fetchone()
        profile_id = int(row[0]) if row else new_flclash_profile_id(con)
        (profiles_dir / f"{profile_id}.yaml").write_text(text, encoding="utf-8")
        if row:
            con.execute(
                "update profiles set label = ?, current_group_name = ?, url = ?, last_update_date = ?, "
                "auto_update = 1, selected_map = ?, unfold_set = ? where id = ?",
                (label, None, public_url, int(time.time()), "{}", "[]", profile_id),
            )
        else:
            con.execute(
                'insert into profiles (id,label,current_group_name,url,last_update_date,overwrite_type,script_id,'
                'auto_update_duration_millis,subscription_info,auto_update,selected_map,unfold_set,"order") '
                "values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (profile_id, label, None, public_url, int(time.time()), "none", None, 0, None, 1, "{}", "[]", None),
            )
        con.commit()
    return {"profileId": profile_id, "label": label, "path": str(profiles_dir / f"{profile_id}.yaml")}


def run(args: argparse.Namespace) -> dict[str, Any]:
    now = datetime.now(ZoneInfo(args.timezone))
    regions = {item.strip().upper() for item in args.regions.split(",") if item.strip()}
    unknown = regions - set(REGION_KEYWORDS)
    if unknown:
        raise ValueError(f"unknown regions: {sorted(unknown)}")
    sources = list(dict.fromkeys([*STATIC_SOURCES, *daily_sources(now)]))
    source_reports: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    seen_candidates: set[tuple[str, int, str]] = set()

    with ThreadPoolExecutor(max_workers=args.fetch_workers) as executor:
        future_map = {executor.submit(fetch_text, source, args.fetch_timeout): source for source in sources}
        for future in as_completed(future_map):
            source = future_map[future]
            _, text, error = future.result()
            if error or text is None:
                source_reports.append({"url": source, "ok": False, "error": error})
                continue
            proxies = parse_clash_proxies(text)
            selected = 0
            for proxy in proxies:
                region = classify_region(proxy, regions)
                if region is None:
                    continue
                try:
                    port = int(proxy.get("port") or 0)
                    proxy["port"] = port
                except Exception:
                    continue
                key = (str(proxy.get("server") or ""), port, str(proxy.get("type") or ""))
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                candidates.append({"proxy": proxy, "region": region, "source": source})
                selected += 1
            source_reports.append({"url": source, "ok": True, "proxies": len(proxies), "selected": selected})

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.test_workers) as executor:
        future_map = {executor.submit(tcp_delay_ms, item["proxy"], args.tcp_timeout_ms): item for item in candidates}
        for future in as_completed(future_map):
            item = future_map[future]
            delay = future.result()
            if delay is not None and delay < args.threshold_ms:
                results.append({**item, "delayMs": delay})
    results.sort(key=lambda item: (int(item["delayMs"]), str(item["region"]), str(item["proxy"].get("name") or "")))
    measured_under_threshold = len(results)
    stable_limit_ms = max(1, args.threshold_ms - args.publish_buffer_ms)
    stable_results = [item for item in results if int(item["delayMs"]) < stable_limit_ms]
    for region in sorted(regions):
        if any(item["region"] == region for item in stable_results):
            continue
        regional_best = next((item for item in results if item["region"] == region), None)
        if regional_best is not None:
            stable_results.append(regional_best)
    stable_results.sort(key=lambda item: (int(item["delayMs"]), str(item["region"]), str(item["proxy"].get("name") or "")))
    results = stable_results
    if args.max_nodes > 0:
        results = results[: args.max_nodes]

    config = build_config(results)
    validation = validate_config(config)
    output = Path(args.output)
    output.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False).replace("!!str ", ""), encoding="utf-8")
    desktop_copy = Path(args.desktop_copy).expanduser() if args.desktop_copy else None
    if desktop_copy:
        desktop_copy.write_text(output.read_text(encoding="utf-8"), encoding="utf-8")
    public_url = f"{REPO_PUBLIC_BASE}/{output.name}"
    flclash = sync_flclash_profile(output, args.flclash_label, public_url) if args.sync_flclash else None
    return {
        "output": str(output.resolve()),
        "publicUrl": public_url,
        "desktopCopy": str(desktop_copy) if desktop_copy else None,
        "regions": sorted(regions),
        "thresholdMs": args.threshold_ms,
        "publishBufferMs": args.publish_buffer_ms,
        "stableLimitMs": stable_limit_ms,
        "sources": len(sources),
        "candidates": len(candidates),
        "measuredUnderThreshold": measured_under_threshold,
        "underThreshold": len(results),
        "regionCounts": {region: sum(1 for item in results if item["region"] == region) for region in sorted(regions)},
        "validation": validation,
        "flclash": flclash,
        "recommended": [
            {
                "name": item["proxy"].get("name"),
                "region": item["region"],
                "type": item["proxy"].get("type"),
                "server": item["proxy"].get("server"),
                "port": item["proxy"].get("port"),
                "delayMs": item["delayMs"],
                "source": item["source"],
            }
            for item in results[:20]
        ],
        "sourcesWithCandidates": [item for item in source_reports if item.get("selected")],
        "failedSources": [item for item in source_reports if not item.get("ok")],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update FlClash SG/JP/TW under-100ms public node profile.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--desktop-copy", default=str(Path.home() / "Desktop/FlClash-SG-JP-TW-100ms.yaml"))
    parser.add_argument("--regions", default=",".join(DEFAULT_REGIONS))
    parser.add_argument("--threshold-ms", type=int, default=DEFAULT_THRESHOLD_MS)
    parser.add_argument("--publish-buffer-ms", type=int, default=20)
    parser.add_argument("--max-nodes", type=int, default=30)
    parser.add_argument("--fetch-timeout", type=float, default=10)
    parser.add_argument("--tcp-timeout-ms", type=int, default=1200)
    parser.add_argument("--fetch-workers", type=int, default=16)
    parser.add_argument("--test-workers", type=int, default=80)
    parser.add_argument("--timezone", default="Asia/Hong_Kong")
    parser.add_argument("--sync-flclash", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--flclash-label", default=DEFAULT_LABEL)
    return parser.parse_args()


def main() -> int:
    summary = run(parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
