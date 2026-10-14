#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每天定时更新 cctv_ws.txt（央视+卫视精简直播源）。

逻辑：
1. 读仓库里已有的 cctv_ws.txt 做基线（首次运行请先把验证好的文件放进去）；
2. 逐个实测基线地址，能播的保留；
3. 基线失效的台，从上游 vbskycn/iptv 的 iptv4.txt 里找同名频道的可播地址替换；
4. 上游新增的频道也一并收录。
纯标准库，GitHub Actions 可直接运行。
"""
import re
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SRC = "https://live.zbds.top/tv/iptv4.txt"
FALLBACK = "https://gh-proxy.com/raw.githubusercontent.com/vbskycn/iptv/refs/heads/master/tv/iptv4.txt"
BASELINE = "YSws.txt"
OUT = "YSws.txt"

JUNK = re.compile(r"\.mp4($|\?)|yao87\.com|264788\.xyz|kwimgs\.com|chunklist_w1266569526\.m3u$|;http|239\.\d+\.\d+\.\d+")
# 频道名黑名单：4K（老电视无硬解）、非大陆卫视、熊猫直播等
JUNK_NAME = re.compile(r"4K|人间卫视|熊猫直播")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CTX = ssl.create_default_context()


def fetch(url, timeout=25, limit=65536):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        if r.status != 200:
            return None
        return r.read(limit)


def playable(url):
    try:
        data = fetch(url, timeout=12, limit=4096)
        if not data:
            return False
        return "#EXTM3U" in data[:2048].decode("utf-8", "ignore")
    except Exception:
        return False


def parse_txt(text):
    """-> [(section, name, url)] 保序，只取央视/卫视两个分组"""
    items, current = [], None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(.*),#genre#$", line)
        if m:
            current = m.group(1)
            continue
        if current in ("央视频道", "卫视频道") and ",http" in line:
            name, url = line.split(",", 1)
            items.append((current, name.strip(), url.strip()))
    return items


def main():
    # 1. 基线
    try:
        with open(BASELINE, encoding="utf-8") as f:
            baseline = parse_txt(f.read())
        print("baseline:", len(baseline), "entries")
    except FileNotFoundError:
        baseline = []
        print("no baseline, start from upstream only")

    # 2. 上游
    raw = None
    for src in (SRC, FALLBACK):
        try:
            raw = fetch(src)
            print("upstream ok:", src, len(raw), "bytes")
            break
        except Exception as e:
            print("upstream fail:", src, e)
    upstream = parse_txt(raw.decode("utf-8", "ignore")) if raw else []
    up_by_name = {}
    for sec, name, url in upstream:
        if JUNK.search(url) or JUNK_NAME.search(name):
            continue
        up_by_name.setdefault(name, []).append(url)

    # 3. 待测：基线地址 + 同名上游候选
    candidates = {}   # name -> [urls...] 保序去重
    order = []        # (section, name) 保序
    for sec, name, url in baseline:
        if name not in candidates:
            candidates[name] = []
            order.append((sec, name))
        if url not in candidates[name]:
            candidates[name].append(url)
    for name, urls in up_by_name.items():
        if name not in candidates:
            candidates[name] = []
            # 新频道归入对应分组
            sec = next((s for s, n, _ in upstream if n == name), "卫视频道")
            order.append((sec, name))
        for u in urls:
            if u not in candidates[name]:
                candidates[name].append(u)

    all_urls = list(dict.fromkeys(u for us in candidates.values() for u in us))
    print("testing", len(all_urls), "urls...")
    ok = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for u, good in zip(all_urls, ex.map(playable, all_urls)):
            ok[u] = good

    # 4. 每频道取第一个能播的
    result, kept, replaced, dropped = [], 0, 0, 0
    base_url = {n: u for _, n, u in baseline}
    for sec, name in order:
        pick = next((u for u in candidates[name] if ok.get(u)), None)
        if pick:
            result.append((sec, name, pick))
            if base_url.get(name) == pick:
                kept += 1
            else:
                replaced += 1
        else:
            dropped += 1
            print("DROP", name)

    by_sec = {"央视频道": [], "卫视频道": []}
    for sec, name, pick in result:
        by_sec.setdefault(sec, []).append((name, pick))
    with open(OUT, "w", encoding="utf-8") as f:
        for sec in ("央视频道", "卫视频道"):
            if not by_sec[sec]:
                continue
            f.write(sec + ",#genre#\n")
            for name, url in by_sec[sec]:
                f.write(name + "," + url + "\n")
    print("kept=%d replaced=%d dropped=%d total=%d -> %s"
          % (kept, replaced, dropped, len(result), OUT))


if __name__ == "__main__":
    main()
