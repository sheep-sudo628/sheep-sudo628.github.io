#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
国考报名人数抓取脚本 (华图 /zw/rank/)
- 抓取指定年份全国所有职位的 报考人数 / 合格人数 / 招考人数
- 按时间戳落盘快照(gzip) + 追加时间序列汇总
- 生成前端数据文件 bmrs_data.js
数据源为服务端渲染 HTML 表格, 无需登录。
用法:
    python scrape_bmrs.py            # 自动探测最新有数据的年份, 抓全国
    python scrape_bmrs.py --year 2026
    python scrape_bmrs.py --limit 5  # 只抓前5页(测试)
"""
import argparse
import gzip
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

sys.stdout.reconfigure(encoding='utf-8')

BASE = 'https://ah.huatu.com/zw/rank/'
DETAIL_BASE = 'https://ah.huatu.com/zw/'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}
PAGE_SIZE = 20
DEFAULT_OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'bmrs')

# 寻找"有数据"的年份, 从新到旧探测
PROBE_YEARS = ['2027', '2026', '2025']


def http_get(session, url, tries=3, timeout=25, sleep=0.6):
    last = None
    for i in range(tries):
        try:
            r = session.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                r.encoding = 'utf-8'
                return r.text
            last = f'HTTP {r.status_code}'
        except Exception as e:  # noqa
            last = repr(e)
        time.sleep(sleep * (i + 1))
    raise RuntimeError(f'GET failed: {url} -> {last}')


def extract_total(html):
    """返回 共N条 的 N, 找不到返回 None"""
    m = re.search(r'共\s*([0-9]{2,})\s*条', html)
    if m:
        return int(m.group(1))
    m = re.search(r'共\s*<[^>]*>\s*([0-9]{2,})', html)
    if m:
        return int(m.group(1))
    return None


def parse_rows(html, year):
    """解析一行职位记录"""
    out = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S)
        if len(cells) < 7:
            continue
        vals = [re.sub(r'<[^>]+>', '', c).replace('&nbsp;', ' ').strip() for c in cells]
        if vals[0] in ('地区', '') or not vals[5]:
            continue
        # 详细链接 -> detail id
        detail_id = None
        m = re.search(rf'\.\./{year}/(\d+)\.html', row)
        if m:
            detail_id = m.group(1)
        def to_int(s):
            s = re.sub(r'[^0-9]', '', s or '')
            return int(s) if s else 0
        out.append({
            'region': vals[0],
            'dept': vals[1],
            'bureau': vals[2],
            'position': vals[3],
            'plan': to_int(vals[4]),
            'apply': to_int(vals[5]),
            'pass': to_int(vals[6]),
            'detail_id': detail_id,
        })
    return out


def probe_year(session, year):
    """探测某年份是否已有数据: 详情链接是否指向该年份"""
    html = http_get(session, f'{BASE}?yr={year}&ks=bmrs&page=1')
    total = extract_total(html)
    has_year = bool(re.search(rf'\.\./{year}/\d+\.html', html))
    return {'year': year, 'total': total, 'has_data': has_year}


def detect_live_year(session):
    """自动探测: 返回 (year, total) —— 取第一个'有数据'的新年份"""
    details = []
    for y in PROBE_YEARS:
        info = probe_year(session, y)
        details.append(info)
        print(f"  探测 {y}: 共{info['total']}条, 数据年份匹配={info['has_data']}")
        if info['has_data'] and info['total']:
            return info['year'], info['total']
    # 全部回退: 用最后一个能解析总数的年份
    for info in details:
        if info['total']:
            return info['year'], info['total']
    raise RuntimeError('无法探测到任何可用年份的数据')


def fetch_page(args):
    """线程任务: 抓取并解析单页, 返回 (page, rows)"""
    year, p = args
    s = requests.Session()  # 每线程独立会话, 避免共享连接池的线程安全问题
    url = f'{BASE}?yr={year}&ks=bmrs&page={p}'
    html = http_get(s, url)
    return p, parse_rows(html, year)


def scrape_year(session, year, total, limit=None, workers=8):
    """并发抓取全国全部职位"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    if limit:
        pages = min(pages, limit)
    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fetch_page, (year, p)) for p in range(1, pages + 1)]
        for fut in as_completed(futs):
            p, page_rows = fut.result()
            results[p] = page_rows
            done += 1
            if done % 100 == 0 or done == pages:
                print(f'    完成 {done}/{pages} 页', flush=True)
    rows = []
    for p in range(1, pages + 1):
        rows.extend(results.get(p, []))
    return rows


def summarize(year, rows):
    total_plan = sum(r['plan'] for r in rows)
    total_apply = sum(r['apply'] for r in rows)
    total_pass = sum(r['pass'] for r in rows)
    ratios = [(r['apply'] / r['plan']) for r in rows if r['plan'] > 0]
    ratios.sort()
    return {
        'year': year,
        'positions': len(rows),
        'total_plan': total_plan,
        'total_apply': total_apply,
        'total_pass': total_pass,
        'avg_ratio': round(total_apply / total_plan, 2) if total_plan else 0,
        'max_ratio': round(ratios[-1], 1) if ratios else 0,
    }


def save_snapshot(outdir, year, ts, rows, summary):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f'bmrs_{year}_{ts}.json.gz')
    payload = {'year': year, 'ts': ts, 'summary': summary, 'rows': rows}
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def append_timeseries(outdir, summary, ts):
    idx_path = os.path.join(outdir, 'timeseries.json')
    data = []
    if os.path.exists(idx_path):
        with open(idx_path, encoding='utf-8') as f:
            data = json.load(f)
    entry = dict(summary)
    entry['ts'] = ts
    data.append(entry)
    with open(idx_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def export_js(outdir, year, ts, rows, summary, timeseries, js_path):
    """生成前端数据文件"""
    # 精简字段, 缩小体积
    compact = [{
        'r': r['region'], 'd': r['dept'], 'b': r['bureau'], 'p': r['position'],
        'n': r['plan'], 'a': r['apply'], 'g': r['pass'],
    } for r in rows]
    payload = {
        'year': year,
        'updateTime': ts,
        'summary': summary,
        'timeseries': timeseries,
        'rows': compact,
    }
    with open(js_path, 'w', encoding='utf-8') as f:
        f.write('// 国考报名数据 - 自动生成, 不要手工编辑\n')
        f.write('window.BMRS_DATA = ')
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')
    return js_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', default=None)
    ap.add_argument('--limit', type=int, default=None, help='仅抓前N页(测试)')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--outdir', default=DEFAULT_OUTDIR)
    args = ap.parse_args()

    session = requests.Session()
    print('== 探测数据年份 ==', flush=True)
    if args.year:
        info = probe_year(session, args.year)
        if not info['total']:
            raise SystemExit(f'{args.year} 无数据')
        year, total = info['year'], info['total']
    else:
        year, total = detect_live_year(session)
    print(f'== 使用年份 {year}, 全国共 {total} 条 ==', flush=True)

    rows = scrape_year(session, year, total, limit=args.limit, workers=args.workers)
    if args.limit:
        print(f'  [测试模式] 仅抓取 {len(rows)} 条', flush=True)
    summary = summarize(year, rows)
    print('== 汇总 ==', flush=True)
    print(' ', json.dumps(summary, ensure_ascii=False), flush=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = save_snapshot(args.outdir, year, ts, rows, summary)
    print('  快照已保存:', path, flush=True)
    if not args.limit:
        append_timeseries(args.outdir, summary, ts)
        idx = load_timeseries(args.outdir)
    else:
        idx = []
        idx_path = os.path.join(args.outdir, 'timeseries.json')
        if os.path.exists(idx_path):
            with open(idx_path, encoding='utf-8') as f:
                idx = json.load(f)
    js_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bmrs_data.js')
    export_js(args.outdir, year, ts, rows, summary, idx, js_path)
    print('  前端数据已生成:', js_path)


def load_timeseries(outdir):
    p = os.path.join(outdir, 'timeseries.json')
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return []


if __name__ == '__main__':
    main()
