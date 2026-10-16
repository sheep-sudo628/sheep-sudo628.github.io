#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
2027 国考报名期 - 每日数据抓取
数据源:
  1) 中公教育 每日全国汇总文章(自动发现)
     - 全国总数: 报考 / 过审 / 待审 / 平均竞争比 / 无人报考职位数
     - 7张统计表: 各省 / 部门Top10 / 报名Top10职位(含职位代码) /
                  职位属性 / 考试类别 / 学历 / 竞争比Top10职位
  2) 华图 31省 renshu 页 -> 市级/区县级汇总(地区/岗位数/招考/报名/竞争比)
输出:
  data/bmrs27/snap_YYYYmmdd_HHMMSS.json  每次快照
  data/bmrs27/series.json                全国总量时间序列
  bmrs27_data.js                         前端数据(latest)
用法: python scrape_2027.py
"""
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlencode

import pandas as pd
import requests

sys.stdout.reconfigure(encoding='utf-8')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}
OFFCN_LIST = 'http://m.offcn.com/gjgwy/kaoshi/4034/'
# 中公职位库 SSR 静态榜: field=bmrs报考/hgrs过审/jzb竞争比, asc=升序(冷门在前)
OFFCN_ZW_ASC = 'https://zw.offcn.com/gj/zwph/2027_{field}_qb_qb_qb_asc.html'
HUATU_BMRS = 'https://ah.huatu.com/zw/bmrs/2027.html'
HUATU_CITY = 'https://ah.huatu.com/zw/{p}/renshu/'
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'bmrs27')
JS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bmrs27_data.js')

PROV_PINYIN = {
    '安徽': 'anhui', '北京': 'beijing', '重庆': 'chongqing', '福建': 'fujian',
    '甘肃': 'gansu', '广东': 'guangdong', '广西': 'guangxi', '贵州': 'guizhou',
    '海南': 'hainan', '河北': 'hebei', '黑龙江': 'heilongjiang', '河南': 'henan',
    '湖北': 'hubei', '湖南': 'hunan', '江苏': 'jiangsu', '江西': 'jiangxi',
    '吉林': 'jilin', '辽宁': 'liaoning', '内蒙古': 'neimenggu', '宁夏': 'ningxia',
    '青海': 'qinghai', '山东': 'shandong', '上海': 'shanghai', '山西': 'shanxi',
    '陕西': 'shanxisheng', '四川': 'sichuan', '天津': 'tianjin', '西藏': 'xicang',
    '新疆': 'xinjiang', '云南': 'yunnan', '浙江': 'zhejiang',
}


def get(session, url, tries=3, timeout=25):
    last = None
    for i in range(tries):
        try:
            r = session.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                r.encoding = r.apparent_encoding or 'utf-8'
                return r.text
            last = f'HTTP {r.status_code}'
        except Exception as e:  # noqa
            last = repr(e)
        time.sleep(0.8 * (i + 1))
    raise RuntimeError(f'GET failed {url}: {last}')


# ==================== 中公 ====================

def parse_ratio(s):
    """'6.1:1' / '7:1' -> 6.1"""
    m = re.search(r'([0-9]+\.?[0-9]*)', str(s))
    return float(m.group(1)) if m else 0.0


def table_to_records(df):
    """第0行表名, 第1行表头, 其后数据 -> (name, records)"""
    name = str(df.iloc[0, 0]).replace('表：', '').replace('分析', '').strip()
    headers = [str(x).strip() for x in df.iloc[1].tolist()]
    recs = []
    for i in range(2, len(df)):
        row = df.iloc[i].tolist()
        rec = {headers[j]: row[j] for j in range(len(headers))}
        recs.append(rec)
    return name, recs


def to_int(v):
    s = re.sub(r'[^0-9]', '', str(v))
    return int(s) if s else 0


def parse_position_cell(s):
    """'资源普查处一级主任科员及以下(200110010002)' -> (职位名, 职位代码)"""
    m = re.search(r'[（(]([0-9]{6,})[）)]', str(s))
    code = m.group(1) if m else ''
    name = re.sub(r'[（(][0-9]{6,}[）)]', '', str(s)).strip()
    return name, code


def find_national_articles(session):
    """栏目页发现候选全国汇总文, 按标题质量优先级排序
    1) 含'平均竞争比'+待审: 完整7表版  2) 含'汇总'+报名人数  3) 报名人数统计+过审/通过审核"""
    html = get(session, OFFCN_LIST)
    pairs = re.findall(
        r'<a[^>]+href=["\'](?:https?:)?(//m\.offcn\.com/gjgwy/2026/\d{4}/\d+\.html)["\'][^>]*>(.*?)</a>',
        html, re.S)
    banned = ['省', '市', '地区', '部门', '局', '海关', '铁路', '气象', '边检', '银行',
              '海事', '监管', '公安', '张家界', '常德',
              # 省份名(防止把省级专版当全国汇总)
              '安徽', '北京', '重庆', '福建', '广东', '甘肃', '广西', '贵州', '海南',
              '河北', '黑龙江', '河南', '湖北', '湖南', '江苏', '江西', '吉林', '辽宁',
              '内蒙古', '宁夏', '青海', '山东', '上海', '山西', '陕西', '四川', '天津',
              '西藏', '新疆', '云南', '浙江']
    c1, c2, c3 = [], [], []
    seen = set()
    for href, txt in pairs:
        title = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', txt)).strip()
        title = re.sub(r'^(HOT|置顶|顶)\s*', '', title)
        if not title or href in seen or any(b in title for b in banned):
            continue
        seen.add(href)
        item = ('https:' + href, title)
        if '平均竞争比' in title and '待审' in title:
            c1.append(item)
        elif '汇总' in title and '报名人数' in title:
            c2.append(item)
        elif '报名人数统计' in title and ('通过审核' in title or '过审' in title):
            c3.append(item)
    return c1 + c2 + c3


def parse_national_article(session, url, title):
    """解析单篇全国汇总文: 总数 + 7张统计表"""
    html = get(session, url)

    # 全国总数(正文优先, 标题兜底)
    def grab(pat, src=None):
        m = re.search(pat, src or html)
        return int(m.group(1)) if m else None

    t = title.replace(',', '').replace('，', '')
    apply_n = (grab(r'共报考\s*([0-9]{3,})\s*人')
               or grab(r'([0-9]{3,})\s*人(?:报名|报考)', t)
               or grab(r'([0-9]{3,})\s*人(?:报名|报考)'))
    m = (re.search(r'([0-9]{3,})\s*人过审', t) or re.search(r'([0-9]{3,})\s*人过审', html)
         or re.search(r'([0-9]{3,})\s*人报名审核通过', html))
    pass_n = int(m.group(1)) if m else None
    pend_n = grab(r'([0-9]{3,})\s*人(?:处于)?待审')
    # 合理性校验: 待审≈报考-过审, 偏差过大说明匹配到文内城市小数据, 弃用(沿用上一快照)
    if pend_n and apply_n and pass_n:
        if abs(pend_n - (apply_n - pass_n)) > max(apply_n, pass_n) * 0.2:
            pend_n = None
    if pass_n and apply_n and pass_n > apply_n:
        pass_n = None
    zero_n = grab(r'([0-9]{2,})\s*个职位无人报考')
    low_n = grab(r'([0-9]{2,})\s*个职位报考人数低于\s*[0-9]+\s*人')
    # 时点: "截止至15日16时" / "截至15日16时"(含标题)
    src_all = t + ' ' + html
    tm = re.search(r'[截止]止?至\s*(\d+)日(\d+)时', src_all)
    asof = f'{tm.group(1)}日{tm.group(2)}时' if tm else ''

    tables = pd.read_html(io.StringIO(html))
    bundle = {}
    for df in tables:
        if df.shape[1] != 5 or df.shape[0] < 3:
            continue
        try:
            name, recs = table_to_records(df)
        except Exception:
            continue
        bundle[name] = recs

    def pick(*keys):
        for k, v in bundle.items():
            if all(key in k for key in keys):
                return v
        return []

    provinces, dept_top, pos_apply_top, attrs, categories, degrees, pos_ratio_top = (
        [], [], [], [], [], [], [])

    for rec in pick('各省'):
        provinces.append({
            'name': str(rec.get('各省', '')).strip(),
            'positions': to_int(rec.get('职位数')),
            'plan': to_int(rec.get('招录人数')),
            'apply': to_int(rec.get('报名人数')),
            'ratio': parse_ratio(rec.get('竞争比')),
        })

    for rec in pick('部门'):
        dept_top.append({
            'name': str(rec.get('部门招录人数（前10）', '')).strip(),
            'positions': to_int(rec.get('职位数')),
            'plan': to_int(rec.get('招录人数')),
            'apply': to_int(rec.get('报名人数')),
            'ratio': parse_ratio(rec.get('竞争比')),
        })

    def parse_pos(recs):
        out = []
        for rec in recs:
            pname, code = parse_position_cell(rec.get('招考职位（部门代码）'))
            out.append({
                'dept': str(rec.get('部门名称（部门代码）', '')).strip(),
                'position': pname,
                'code': code,
                'plan': to_int(rec.get('招录人数')),
                'apply': to_int(rec.get('报名人数')),
                'ratio': parse_ratio(rec.get('竞争比')),
            })
        return out

    pos_apply_top = parse_pos(pick('报名人数前10职位'))
    pos_ratio_top = parse_pos(pick('竞争比前10职位'))

    for rec in pick('职位属性'):
        attrs.append({'name': str(rec.get('职位属性', '')).strip(),
                      'positions': to_int(rec.get('职位数')), 'plan': to_int(rec.get('招录人数')),
                      'apply': to_int(rec.get('报名人数')), 'ratio': parse_ratio(rec.get('竞争比'))})
    for rec in pick('考试类别'):
        categories.append({'name': str(rec.get('考试类别', '')).strip(),
                           'positions': to_int(rec.get('职位数')), 'plan': to_int(rec.get('招录人数')),
                           'apply': to_int(rec.get('报名人数')), 'ratio': parse_ratio(rec.get('竞争比'))})
    for rec in pick('学历'):
        degrees.append({'name': str(rec.get('学历', '')).strip(),
                        'positions': to_int(rec.get('职位数')), 'plan': to_int(rec.get('招录人数')),
                        'apply': to_int(rec.get('报名人数')), 'ratio': parse_ratio(rec.get('竞争比'))})

    return {
        'article_url': url,
        'article_title': title,
        'asof': asof,
        'national': {
            'apply': apply_n, 'pass': pass_n, 'pend': pend_n,
            'zero_positions': zero_n, 'low_positions': low_n,
        },
        'provinces': provinces,
        'dept_top': dept_top,
        'pos_apply_top': pos_apply_top,
        'pos_ratio_top': pos_ratio_top,
        'attrs': attrs,
        'categories': categories,
        'degrees': degrees,
    }


def scrape_offcn(session):
    """按优先级逐个尝试候选全国汇总文, 取第一篇含完整省表的; 全缺表时兜底取有总数的一篇"""
    candidates = find_national_articles(session)
    if not candidates:
        raise RuntimeError('未找到全国汇总文章')
    best_partial = None
    for url, title in candidates:
        try:
            data = parse_national_article(session, url, title)
        except Exception as e:  # noqa
            print('  候选文解析失败:', title, repr(e), flush=True)
            continue
        print('  全国汇总文:', title, flush=True)
        if data['provinces'] and data['national']['apply']:
            return data
        if data['national']['apply'] and best_partial is None:
            best_partial = data
    if best_partial:
        print('  警告: 暂无含完整省表的汇总文, 部分数据沿用上一快照', flush=True)
        return best_partial
    raise RuntimeError('全国汇总文章均解析失败')


OFFCN_DETAIL = 'https://zw.offcn.com/gj/2027/zw{pid}.html'
# 中公地区代码 -> 省名 (职位排行页按地区过滤用)
OFFCN_PROV = {
    'ah': '安徽', 'bj': '北京', 'cq': '重庆', 'fj': '福建', 'gd': '广东', 'gs': '甘肃',
    'gx': '广西', 'gz': '贵州', 'hb': '河北', 'he': '河南', 'hi': '海南', 'hlj': '黑龙江',
    'hn': '湖南', 'hu': '湖北', 'jl': '吉林', 'js': '江苏', 'jx': '江西', 'ln': '辽宁',
    'nm': '内蒙古', 'nx': '宁夏', 'qh': '青海', 'sa': '陕西', 'sc': '四川', 'sd': '山东',
    'sh': '上海', 'sx': '山西', 'tj': '天津', 'xj': '新疆', 'xz': '西藏', 'yn': '云南',
    'zj': '浙江',
}
# 省级职位排行页: field=bmrs报考/jzb竞争比, sort=asc|desc
OFFCN_PROV_RANK = 'https://zw.offcn.com/gj/zwph/2027_{field}_{code}_qb_qb_{sort}.html'
_detail_cache = {}


def parse_offcn_detail(html):
    """职位详情页 -> 官方职位代码/招考/报考/过审 + 画像字段(地区/政治面貌/基层年限/简介)"""
    info = {}
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        vals = [re.sub(r'<[^>]+>', '', c).replace('&nbsp;', ' ').strip()
                for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)]
        vals = [v for v in vals if v]
        for i in range(0, len(vals) - 1, 2):
            info.setdefault(vals[i], vals[i + 1])

    def num_after(label):
        v = info.get(label, '')
        m = re.search(r'\d+', v)
        return int(m.group(0)) if m else None

    return {
        'code': info.get('职位代码', '').strip() if re.fullmatch(r'\d{6,}',
                 info.get('职位代码', '').strip()) else '',
        'plan': num_after('招考人数'),
        'apply': num_after('报考人数'),
        'pass': num_after('过审人数'),
        'region': info.get('地区', '').strip(),
        'party': info.get('政治面貌', '').strip(),
        'exp': info.get('基层工作最低年限', '').strip(),
        'intro': info.get('职位简介', '').strip(),
    }


def parse_rank_rows(html):
    """职位排行页 -> 记录列表; bmrs 模板不渲染数字列时 plan/apply/pass 为 None"""
    out = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)
        if len(cells) < 4:
            continue
        vals = [re.sub(r'<[^>]+>', '', c).replace('&nbsp;', ' ').strip() for c in cells]
        if not vals[0] or vals[0] == '地区':
            continue
        idm = re.search(r"urljump\('zw','gj','2027','(\d+)'\)", tr)
        if not idm:
            continue
        rec = {'zw_id': idm.group(1), 'region': vals[0], 'dept': vals[1],
               'bureau': vals[2], 'position': vals[3],
               'plan': None, 'apply': None, 'pass': None}
        nums = vals[4:7]
        if len(nums) == 3 and all(re.fullmatch(r'\d+', x) for x in nums):
            rec['plan'], rec['apply'], rec['pass'] = int(nums[0]), int(nums[1]), int(nums[2])
        out.append(rec)
    return out


def fetch_details(pids):
    """并发抓职位详情(带缓存), 补全列表页缺失的数字与职位代码"""
    need = [p for p in dict.fromkeys(pids) if p and p not in _detail_cache]
    if not need:
        return

    def one(pid):
        s = requests.Session()
        try:
            return pid, parse_offcn_detail(get(s, OFFCN_DETAIL.format(pid=pid)))
        except Exception:  # noqa
            return pid, {}

    with ThreadPoolExecutor(max_workers=12) as ex:
        for pid, d in ex.map(one, need):
            _detail_cache[pid] = d


def enrich_offcn(r, mode='apply'):
    """列表页记录 + 详情缓存 -> 统一记录(含职位代码)
    mode='apply' 竞争比=报考/招录; mode='pass' 竞争比=过审/招录(中公职位库口径)"""
    d = _detail_cache.get(r['zw_id'], {})
    plan = r['plan'] if r['plan'] is not None else d.get('plan')
    apply_v = r['apply'] if r['apply'] is not None else d.get('apply')
    pass_v = r['pass'] if r['pass'] is not None else d.get('pass')
    base = pass_v if mode == 'pass' else apply_v
    ratio = round(base / plan, 2) if plan and base is not None \
        else (0.0 if base == 0 else None)
    return {'region': r['region'], 'dept': r['dept'], 'bureau': r['bureau'],
            'position': r['position'], 'code': d.get('code', ''),
            'plan': plan, 'apply': apply_v, 'pass': pass_v, 'ratio': ratio}


def scrape_offcn_cold(session):
    """全国冷门职位榜: 报考最少(bmrs升序) + 竞争比最低(jzb升序), 各前10"""
    bmrs_rows = parse_rank_rows(get(session, OFFCN_ZW_ASC.format(field='bmrs')))[:10]
    jzb_rows = parse_rank_rows(get(session, OFFCN_ZW_ASC.format(field='jzb')))[:10]
    fetch_details([r['zw_id'] for r in bmrs_rows + jzb_rows])
    min_apply = [enrich_offcn(r, 'apply') for r in bmrs_rows]
    min_ratio = [enrich_offcn(r, 'pass') for r in jzb_rows]
    hit = sum(1 for r in min_apply + min_ratio if r['plan'] is not None)
    print(f'   冷门榜: 报考最少{len(min_apply)} 竞争比最低{len(min_ratio)} '
          f'详情补全 {hit}/{len(min_apply) + len(min_ratio)}', flush=True)
    return min_apply, min_ratio


def scrape_offcn_prov(session):
    """各省职位排行(按地区): 竞争比最高/最低(jzb, 带数字) + 报考最少(bmrs升序, 详情补数字)
    返回 (ratio_high, ratio_low, min_apply) 三个 {省名: [记录]} 字典
    """
    def one(code, field, sort):
        s = requests.Session()
        try:
            html = get(s, OFFCN_PROV_RANK.format(field=field, code=code, sort=sort))
            return parse_rank_rows(html)[:10]
        except Exception:  # noqa
            return []

    buckets = {'hi': {}, 'lo': {}, 'ma': {}}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {}
        for code in OFFCN_PROV:
            futs[ex.submit(one, code, 'jzb', 'desc')] = ('hi', code)
            futs[ex.submit(one, code, 'jzb', 'asc')] = ('lo', code)
            futs[ex.submit(one, code, 'bmrs', 'asc')] = ('ma', code)
        for f in as_completed(futs):
            kind, code = futs[f]
            buckets[kind][code] = f.result()

    # 报考最少列表页无数字 -> 由详情补全(顺带取回职位代码)
    fetch_details([r['zw_id'] for rows in buckets['ma'].values() for r in rows])

    ratio_high, ratio_low, min_apply = {}, {}, {}
    for code, prov in OFFCN_PROV.items():
        ratio_high[prov] = [enrich_offcn(r, 'pass') for r in buckets['hi'].get(code, [])]
        ratio_low[prov] = [enrich_offcn(r, 'pass') for r in buckets['lo'].get(code, [])]
        min_apply[prov] = [enrich_offcn(r, 'apply') for r in buckets['ma'].get(code, [])]
    print(f'   省榜: 竞争比高{sum(len(v) for v in ratio_high.values())} '
          f'低{sum(len(v) for v in ratio_low.values())} '
          f'报考最少{sum(len(v) for v in min_apply.values())} '
          f'详情缓存{len(_detail_cache)}', flush=True)
    return ratio_high, ratio_low, min_apply


# ==================== 我的条件职位池 ====================

# 中公职位库检索(仅 zhuanye 服务端生效), 分页参数 page
OFFCN_SEARCH = 'https://zw.offcn.com/index/country/search/index'
_MUNI = ('北京', '上海', '天津', '重庆')
PROFILE_MAJOR = '电子信息类'


def parse_loc(work):
    """'安徽省淮北市濉溪县' -> (省, 市, 区县); 直辖市 市=直辖市名"""
    w = (work or '').strip()
    if not w:
        return '', '', ''
    prov = ''
    for p in PROV_PINYIN:
        if w.startswith(p):
            prov = p
            break
    if not prov:
        return '', '', ''
    rest = re.sub(r'^(省|市|维吾尔自治区|壮族自治区|回族自治区|自治区|特别行政区)',
                  '', w[len(prov):])
    if prov in _MUNI:
        return prov, prov, rest
    m = re.match(r'^(.*?(?:市|自治州|地区|盟))', rest)
    city = m.group(1) if m else ''
    return prov, city, rest[len(city):]


def _cell_text(c):
    """取单元格文本; 专业列同时有 .text 与 .cont 会重复, 优先取 .text"""
    m = re.search(r'class="text"[^>]*>(.*?)</', c, re.S)
    if m:
        c = m.group(1)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', c).replace('&nbsp;', ' ')).strip()


def parse_search_rows(html):
    """中公检索结果页 -> 基础职位记录(含 pid/报考/专业/学历/工作地点)"""
    out = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        m = re.search(r"urljump\('zw','gj','2027','(\d+)'\)", tr)
        if not m:
            continue
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)
        vals = [_cell_text(c) for c in cells]
        if len(vals) < 10:
            continue
        apply_v = to_int(vals[6])
        if apply_v > 5_000_000:   # 防杂质(职位代码/序号被当人数)
            continue
        out.append({
            'pid': m.group(1),
            'dept': vals[1], 'bureau': vals[2], 'position': vals[3],
            'code': vals[4], 'plan': to_int(vals[5]), 'apply': apply_v,
            'major': vals[7], 'edu': vals[8], 'work': vals[9],
        })
    return out


def scrape_profile_pool(session, major=PROFILE_MAJOR):
    """我符合条件的职位池: 检索 zhuanye=电子信息类 翻完全部页 + 并发详情补画像字段
    返回记录含 region/city/district/party/exp/fem(是否限女性) 等"""
    base = {'zwcode': 'gj', 'zwyear': '2027', 'tjflag': '1', 'zhuanye': major,
            'zzmm': '', 'xueli': '', 'xuewei': '', 'zyzx': '', 'gznx': '',
            'fwjc': '', 'areaname': '', 'zhuanyebx': ''}

    def page_url(p):
        params = dict(base)
        params['page'] = p
        return OFFCN_SEARCH + '?' + urlencode(params)

    html1 = get(session, page_url(1))
    recs = parse_search_rows(html1)
    pages = [int(x) for x in re.findall(r'[?&]page=(\d+)', html1)]
    maxp = max(pages) if pages else 1
    print(f'   检索「{major}」共 {maxp} 页, 首页 {len(recs)} 条', flush=True)

    def one(p):
        s = requests.Session()
        try:
            return parse_search_rows(get(s, page_url(p)))
        except Exception:  # noqa
            return []

    if maxp > 1:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for rows in ex.map(one, range(2, maxp + 1)):
                recs.extend(rows)
    # 按 pid 去重
    uniq = {}
    for r in recs:
        uniq.setdefault(r['pid'], r)
    recs = list(uniq.values())
    print(f'   列表累计 {len(recs)} 条, 开始并发抓详情…', flush=True)

    fetch_details([r['pid'] for r in recs])

    for r in recs:
        d = _detail_cache.get(r['pid'], {})
        prov, city, dist = parse_loc(r['work'])
        r['region'], r['city'], r['district'] = prov, city, dist
        r['party'] = d.get('party', '')
        r['exp'] = d.get('exp', '')
        r['pass'] = d.get('pass')
        r['fem'] = 1 if re.search(r'限女性|仅限女性|限女', d.get('intro', '')) else 0
        r['ratio'] = round(r['apply'] / r['plan'], 2) if r['plan'] else 0.0
    print(f'   画像职位池 {len(recs)} 条, 详情缓存 {len(_detail_cache)}', flush=True)
    return recs


# ==================== 华图 市级/区县级 ====================

def parse_city_table(html):
    """renshu 页含3张表: 仅第1张是市/区县统计表;
    后2张是职位排名表(职位代码会被误当人数), 必须只取第1张"""
    tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.S)
    if not tables:
        return []
    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tables[0], re.S):
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)
        if len(cells) < 5:
            continue
        vals = [re.sub(r'<[^>]+>', '', c).replace('&nbsp;', ' ').strip() for c in cells]
        if vals[0] in ('地区', '排名', '') or '岗位数' in vals[1]:
            continue
        # 数字列合理性: 单区县报名数不可能超过100万(防杂质)
        apply_v = to_int(vals[3])
        if apply_v > 1_000_000:
            continue
        try:
            rows.append({
                'name': vals[0],
                'positions': to_int(vals[1]),
                'plan': to_int(vals[2]),
                'apply': apply_v,
                'ratio': parse_ratio(vals[4]),
            })
        except Exception:
            continue
    return rows


def parse_hot_table(html):
    """renshu 页第2张表: 该省报考人数Top10职位
    列: 排名/地区/职位代码/招考职位/职位名称/招考人数/报考人数/详情"""
    tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.S)
    if len(tables) < 2:
        return []
    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tables[1], re.S):
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)
        vals = [re.sub(r'<[^>]+>', '', c).replace('&nbsp;', ' ').strip() for c in cells]
        if len(vals) < 7 or vals[0] in ('排名', ''):
            continue
        code = vals[2] if re.fullmatch(r'\d{6,}', vals[2]) else ''
        plan, apply_v = to_int(vals[5]), to_int(vals[6])
        rows.append({
            'region': vals[1], 'code': code, 'dept': vals[3], 'position': vals[4],
            'plan': plan, 'apply': apply_v,
            'ratio': round(apply_v / plan, 2) if plan else 0.0,
        })
    return rows


def fetch_one_city(session_name, prov):
    s = requests.Session()
    html = get(s, HUATU_CITY.format(p=session_name))
    return prov, parse_city_table(html), parse_hot_table(html)


def scrape_huatu_cities():
    cities, prov_hot = {}, {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_one_city, py, prov): prov for prov, py in PROV_PINYIN.items()}
        done = 0
        for fut in as_completed(futs):
            prov, rows, hot = fut.result()
            cities[prov] = rows
            prov_hot[prov] = hot
            done += 1
            if done % 10 == 0 or done == len(futs):
                print(f'    华图市级页 {done}/31', flush=True)
    return cities, prov_hot


def scrape_huatu_provinces(session):
    """华图 bmrs/2027 省级表(与中公口径互为参照)"""
    html = get(session, HUATU_BMRS)
    out = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)
        if len(cells) < 5:
            continue
        vals = [re.sub(r'<[^>]+>', '', c).replace('&nbsp;', ' ').strip() for c in cells]
        if vals[0] not in PROV_PINYIN:
            continue
        out.append({
            'name': vals[0],
            'positions': to_int(vals[1]),
            'plan': to_int(vals[2]),
            'apply': to_int(vals[3]),
            'ratio': parse_ratio(vals[4]),
        })
    return out


# ==================== 主流程 ====================

def load_prev_snapshot():
    """最近一份历史快照(不含本次), 用于新数据缺项时兜底"""
    try:
        names = sorted(f for f in os.listdir(OUTDIR)
                       if re.fullmatch(r'snap_\d{8}_\d{6}\.json', f))
    except OSError:
        return {}
    for name in reversed(names):
        try:
            with open(os.path.join(OUTDIR, name), encoding='utf-8') as f:
                return json.load(f)
        except Exception:  # noqa
            continue
    return {}


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    session = requests.Session()
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    print('== 1. 中公全国汇总 ==', flush=True)
    offcn = scrape_offcn(session)
    n = offcn['national']
    print(f"   截至{offcn['asof']}: 报考{n['apply']} 过审{n['pass']} 待审{n['pend']} "
          f"无人报考{n['zero_positions']}", flush=True)
    print(f"   省表{len(offcn['provinces'])} 部门Top{len(offcn['dept_top'])} "
          f"职位Top{len(offcn['pos_apply_top'])}/{len(offcn['pos_ratio_top'])} "
          f"属性{len(offcn['attrs'])} 类别{len(offcn['categories'])} 学历{len(offcn['degrees'])}",
          flush=True)

    print('== 1b. 中公冷门职位榜 ==', flush=True)
    try:
        min_apply_top, min_ratio_top = scrape_offcn_cold(session)
    except Exception as e:  # noqa
        print('   冷门榜抓取失败:', repr(e), flush=True)
        min_apply_top, min_ratio_top = [], []

    print('== 2. 华图省级/市级 ==', flush=True)
    ht_prov = scrape_huatu_provinces(session)
    cities, prov_hot = scrape_huatu_cities()
    city_cnt = sum(len(v) for v in cities.values())
    hot_cnt = sum(len(v) for v in prov_hot.values())
    print(f'   省表{len(ht_prov)} 市级/区县记录 {city_cnt} 各省热门职位 {hot_cnt}', flush=True)

    print('== 2b. 中公省级职位榜 ==', flush=True)
    try:
        prov_ratio_high, prov_ratio_low, prov_min_apply = scrape_offcn_prov(session)
    except Exception as e:  # noqa
        print('   省榜抓取失败:', repr(e), flush=True)
        prov_ratio_high, prov_ratio_low, prov_min_apply = {}, {}, {}

    print('== 3. 我的条件职位池 ==', flush=True)
    try:
        profile_pool = scrape_profile_pool(session)
    except Exception as e:  # noqa
        print('   画像职位池抓取失败:', repr(e), flush=True)
        profile_pool = []

    # 2027 招考总量(静态)
    total_positions = sum(p['positions'] for p in offcn['provinces']) or 19426
    total_plan = sum(p['plan'] for p in offcn['provinces']) or 35691
    nat = offcn['national']
    nat.update({
        'positions_total': 19426,
        'plan_total': 35691,
        'avg_ratio': round(nat['apply'] / total_plan, 2) if nat.get('apply') else None,
        'approve_rate': round(nat['pass'] / nat['apply'] * 100, 1)
        if nat.get('apply') and nat.get('pass') else None,
    })

    payload = {
        'year': '2027',
        'ts': ts,
        'asof': offcn['asof'],
        'sources': {'offcn': offcn['article_url'], 'huatu': HUATU_BMRS},
        'national': nat,
        'provinces': offcn['provinces'],
        'huatu_provinces': ht_prov,
        'cities': cities,
        'dept_top': offcn['dept_top'],
        'pos_apply_top': offcn['pos_apply_top'],
        'pos_ratio_top': offcn['pos_ratio_top'],
        'min_apply_top': min_apply_top,
        'min_ratio_top': min_ratio_top,
        'prov_hot': prov_hot,
        'prov_ratio_high': prov_ratio_high,
        'prov_ratio_low': prov_ratio_low,
        'prov_min_apply': prov_min_apply,
        'profile_pool': profile_pool,
        'profile_major': PROFILE_MAJOR,
        'attrs': offcn['attrs'],
        'categories': offcn['categories'],
        'degrees': offcn['degrees'],
    }

    # 当日汇总文缺完整表/总数时, 沿用上一快照对应字段, 避免覆盖为空
    prev = load_prev_snapshot()
    if prev:
        for k in ('apply', 'pass', 'pend', 'zero_positions', 'low_positions'):
            if not nat.get(k):
                nat[k] = (prev.get('national') or {}).get(k)
        if not payload['asof']:
            payload['asof'] = prev.get('asof') or ''
        for key in ('provinces', 'dept_top', 'pos_apply_top', 'pos_ratio_top',
                    'min_apply_top', 'min_ratio_top', 'attrs', 'categories', 'degrees'):
            if not payload[key]:
                payload[key] = prev.get(key) or []
        total_positions = sum(p['positions'] for p in payload['provinces']) or 19426
        total_plan = sum(p['plan'] for p in payload['provinces']) or 35691
        nat['avg_ratio'] = round(nat['apply'] / total_plan, 2) if nat.get('apply') else None
        nat['approve_rate'] = (round(nat['pass'] / nat['apply'] * 100, 1)
                               if nat.get('apply') and nat.get('pass') else None)
        if any(payload[k] for k in ('provinces', 'dept_top')):
            print('   (部分字段沿用上一快照)', flush=True)

    snap = os.path.join(OUTDIR, f'snap_{ts}.json')
    with open(snap, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    print('  快照:', snap, flush=True)

    # 时间序列
    series_path = os.path.join(OUTDIR, 'series.json')
    series = []
    if os.path.exists(series_path):
        with open(series_path, encoding='utf-8') as f:
            series = json.load(f)
    series.append({
        'ts': ts, 'asof': payload['asof'] or offcn['asof'],
        'apply': nat['apply'], 'pass': nat['pass'], 'pend': nat['pend'],
    })
    # 同一发布时点(asof)只保留最新一次抓取
    dedup = {}
    for e in series:
        dedup[e.get('asof') or e['ts']] = e
    series = sorted(dedup.values(), key=lambda e: e['ts'])
    with open(series_path, 'w', encoding='utf-8') as f:
        json.dump(series, f, ensure_ascii=False)
    payload['series'] = series

    with open(JS_PATH, 'w', encoding='utf-8') as f:
        f.write('// 2027国考报名数据 - 自动生成, 不要手工编辑\n')
        f.write('window.BMRS27 = ')
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')
    print('  前端数据:', JS_PATH, flush=True)


if __name__ == '__main__':
    main()
