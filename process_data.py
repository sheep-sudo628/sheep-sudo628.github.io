import pandas as pd
import numpy as np
import json
import re

# ============ 配置 ============
file1 = r'e:\Users\61402\xwechat_files\wxid_yn56hzya6mu622_70ce\msg\file\2026-10\21-26国家公务员最低进面分数线.xlsx'
file2 = r'e:\Users\61402\xwechat_files\wxid_yn56hzya6mu622_70ce\msg\file\2026-10\中央机关及其直属机构2027年度考试录用公务员招考简章(1).xls'
out_js = r'd:\py\list\report_data.js'

# 省份名称映射（全称 -> 简称）
PROVINCES = {
    '北京市': '北京', '天津市': '天津', '上海市': '上海', '重庆市': '重庆',
    '河北省': '河北', '山西省': '山西', '辽宁省': '辽宁', '吉林省': '吉林',
    '黑龙江省': '黑龙江', '江苏省': '江苏', '浙江省': '浙江', '安徽省': '安徽',
    '福建省': '福建', '江西省': '江西', '山东省': '山东', '河南省': '河南',
    '湖北省': '湖北', '湖南省': '湖南', '广东省': '广东', '海南省': '海南',
    '四川省': '四川', '贵州省': '贵州', '云南省': '云南', '陕西省': '陕西',
    '甘肃省': '甘肃', '青海省': '青海', '台湾省': '台湾',
    '内蒙古自治区': '内蒙古', '广西壮族自治区': '广西', '西藏自治区': '西藏',
    '宁夏回族自治区': '宁夏', '新疆维吾尔自治区': '新疆',
    '香港特别行政区': '香港', '澳门特别行政区': '澳门',
}

# 各年份分数列名和表头行
YEAR_CONFIG = {
    '2026': {'score_col': '最低进面分数', 'header': 0, 'loc_col': '工作地点'},
    '2025': {'score_col': '进面分', 'header': 1, 'loc_col': '地区'},
    '2024': {'score_col': '分数线', 'header': 1, 'loc_col': '地区'},
    '2023': {'score_col': '最低进面分', 'header': 1, 'loc_col': '工作地点'},
    '2022': {'score_col': '入面分数', 'header': 1, 'loc_col': '工作地点'},
    '2021': {'score_col': '最低进面分数', 'header': 1, 'loc_col': '工作地点'},
}
YEARS = ['2021', '2022', '2023', '2024', '2025', '2026']

# 城市类型后缀
CITY_SUFFIXES = ['市', '地区', '盟', '自治州', '特别行政区']

def parse_location(loc):
    """解析工作地点为 (省, 市, 区)"""
    if pd.isna(loc) or not isinstance(loc, str):
        return (None, None, None)
    loc = loc.strip()
    if not loc:
        return (None, None, None)

    # 匹配省份
    province = None
    province_full = None
    for full, short in PROVINCES.items():
        if loc.startswith(full):
            province = short
            province_full = full
            break
        elif loc == short:
            return (short, None, None)
    if province is None:
        # 可能是简称开头
        for full, short in PROVINCES.items():
            if loc.startswith(short) and len(loc) > len(short):
                province = short
                province_full = full
                break
        if province is None:
            # 尝试匹配简称
            for short in PROVINCES.values():
                if loc == short:
                    return (short, None, None)
            return (None, None, None)

    # 移除省份部分
    if province_full and loc.startswith(province_full):
        rest = loc[len(province_full):]
    else:
        rest = loc[len(province):]

    if not rest:
        return (province, None, None)

    # 匹配城市
    city = None
    for suffix in CITY_SUFFIXES:
        idx = rest.find(suffix)
        if idx > 0:
            city = rest[:idx + len(suffix)]
            rest = rest[idx + len(suffix):]
            break

    # 直辖市特殊处理：直辖市没有地级市层级，区县级直接作为"市"级
    MUNICIPALITIES = {'北京', '天津', '上海', '重庆'}
    if city is None:
        if province in MUNICIPALITIES and rest:
            # 直辖市：rest 是区/县，作为市级
            return (province, rest, None)
        # 其他省份：没有明确城市后缀，整个rest作为区县级
        return (province, None, rest)

    district = rest if rest else None
    return (province, city, district)

def safe_numeric(s):
    return pd.to_numeric(s, errors='coerce')

# ============ 读取历年数据 ============
print('读取历年数据...')
all_scores = []
for year in YEARS:
    cfg = YEAR_CONFIG[year]
    df = pd.read_excel(file1, sheet_name=year, header=cfg['header'])
    df['年份'] = int(year)
    df['分数'] = safe_numeric(df[cfg['score_col']])
    locs = df[cfg['loc_col']].apply(parse_location)
    df['省'] = [l[0] for l in locs]
    df['市'] = [l[1] for l in locs]
    df['区'] = [l[2] for l in locs]
    df['招考人数'] = safe_numeric(df.get('招考人数', 0)).fillna(0).astype(int)
    # 部门代码
    if '部门代码' in df.columns:
        df['部门代码'] = df['部门代码'].astype(str).str.strip()
    else:
        df['部门代码'] = ''
    # 职位代码
    if '职位代码' in df.columns:
        df['职位代码'] = df['职位代码'].astype(str).str.strip()
    else:
        df['职位代码'] = ''
    # 部门名称
    dcol = '招录机关' if '招录机关' in df.columns else '部门名称'
    df['部门名称'] = df[dcol].astype(str).str.strip() if dcol in df.columns else ''
    # 职位名称
    pcol = '招考职位' if '招考职位' in df.columns else ('职位名称' if '职位名称' in df.columns else None)
    df['职位名称'] = df[pcol].astype(str).str.strip() if pcol else ''
    valid = df[df['分数'].notna() & df['省'].notna()].copy()
    all_scores.append(valid[['年份', '省', '市', '区', '分数', '部门代码', '职位代码', '部门名称', '职位名称']])
    print(f'  {year}: 总{len(df)}条, 有效{len(valid)}条')

scores_df = pd.concat(all_scores, ignore_index=True)
print(f'合并: {len(scores_df)} 条有效记录')

# ============ 按地理层级聚合 ============
print('聚合地理数据...')

def aggregate(df, group_cols):
    g = df.groupby(group_cols)['分数'].agg(['min', 'max', 'mean', 'median', 'count']).reset_index()
    g.columns = group_cols + ['最低分', '最高分', '平均分', '中位数', '职位数']
    for c in ['最低分', '最高分', '平均分', '中位数']:
        g[c] = g[c].round(1)
    return g

province_stats = aggregate(scores_df, ['省'])
city_stats = aggregate(scores_df[scores_df['市'].notna()], ['省', '市'])
district_stats = aggregate(scores_df[scores_df['区'].notna()], ['省', '市', '区'])

print(f'  省级: {len(province_stats)}, 市级: {len(city_stats)}, 区县级: {len(district_stats)}')

# ============ 区县分数分布数据（直方图+箱线图） ============
print('计算区县分数分布...')
district_dist = []
for (prov, city, dist), grp in scores_df[scores_df['区'].notna()].groupby(['省', '市', '区']):
    scores = grp['分数'].values
    if len(scores) == 0:
        continue
    # 箱线图统计
    q1 = float(np.percentile(scores, 25))
    median = float(np.percentile(scores, 50))
    q3 = float(np.percentile(scores, 75))
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    outliers = scores[(scores < lower) | (scores > upper)]
    whisker_low = float(scores[scores >= lower].min()) if any(scores >= lower) else float(scores.min())
    whisker_high = float(scores[scores <= upper].max()) if any(scores <= upper) else float(scores.max())

    # 直方图分箱（10个区间）
    s_min, s_max = float(scores.min()), float(scores.max())
    bins = np.linspace(s_min, s_max, 11)
    hist, _ = np.histogram(scores, bins=bins)
    hist_data = []
    for i in range(len(hist)):
        hist_data.append({
            'min': round(float(bins[i]), 1),
            'max': round(float(bins[i+1]), 1),
            'count': int(hist[i])
        })

    district_dist.append({
        '省': prov,
        '市': city,
        '区': dist,
        '职位数': int(len(scores)),
        '最低分': round(s_min, 1),
        '最高分': round(s_max, 1),
        '平均分': round(float(scores.mean()), 1),
        '中位数': round(median, 1),
        'Q1': round(q1, 1),
        'Q3': round(q3, 1),
        '须下限': round(whisker_low, 1),
        '须上限': round(whisker_high, 1),
        '异常值数': int(len(outliers)),
        '直方图': hist_data
    })

print(f'  区县分布数据: {len(district_dist)} 条')

# ============ 读取2027职位并匹配历年分数 ============
print('读取2027职位...')
xl2 = pd.ExcelFile(file2)
sheets = [s for s in xl2.sheet_names if s != 'Sheet1']
df2027 = pd.concat([pd.read_excel(file2, sheet_name=s, header=1) for s in sheets], ignore_index=True)
df2027['部门代码'] = df2027['部门代码'].astype(str).str.strip()
df2027['职位代码'] = df2027['职位代码'].astype(str).str.strip()
print(f'  2027: {len(df2027)} 条')

# 匹配历年分数：2026用职位代码，其他年份用部门名称+职位名称
print('匹配历年分数...')
df2027['部门名称'] = df2027['部门名称'].astype(str).str.strip()
df2027['招考职位'] = df2027['招考职位'].astype(str).str.strip()

for year in YEARS:
    col = f'{year}最低进面分'
    ydf = scores_df[scores_df['年份'] == int(year)]

    if year == '2026':
        # 按职位代码匹配
        ydf_p = ydf[ydf['职位代码'] != ''].groupby('职位代码')['分数'].min().reset_index()
        ydict = dict(zip(ydf_p['职位代码'], ydf_p['分数']))
        df2027[col] = df2027['职位代码'].map(ydict)
    else:
        # 按部门名称+职位名称匹配
        ydf_key = ydf[(ydf['部门名称'] != '') & (ydf['职位名称'] != '')].copy()
        ydf_key['match_key'] = ydf_key['部门名称'] + '||' + ydf_key['职位名称']
        ydf_g = ydf_key.groupby('match_key')['分数'].min().reset_index()
        ydict = dict(zip(ydf_g['match_key'], ydf_g['分数']))
        df2027['_key'] = df2027['部门名称'] + '||' + df2027['招考职位']
        df2027[col] = df2027['_key'].map(ydict)

    matched = df2027[col].notna().sum()
    print(f'  {year}: 匹配{matched}/{len(df2027)} ({matched/len(df2027)*100:.0f}%)')

df2027.drop(columns=['_key'], inplace=True, errors='ignore')

# 保留需要的列
keep_cols = ['部门代码', '职位代码', '用人司局', '招考人数', '专业', '学历',
             '基层工作最低年限', '工作地点', '备注'] + [f'{y}最低进面分' for y in YEARS]
table_data = df2027[keep_cols].copy()
table_data['招考人数'] = table_data['招考人数'].fillna(0).astype(int)
# 分数列转为float
for y in YEARS:
    table_data[f'{y}最低进面分'] = table_data[f'{y}最低进面分'].round(1)

# ============ 输出JS数据文件 ============
print('生成数据文件...')

# 地图数据
map_data = {
    'province': province_stats.to_dict('records'),
    'city': city_stats.to_dict('records'),
    'district': district_stats.to_dict('records'),
    'district_dist': district_dist,
}

# 表格数据
table_records = table_data.to_dict('records')

# 生成JS
js_content = '// 国考分析数据 - 自动生成\n'
js_content += 'window.MAP_DATA = ' + json.dumps(map_data, ensure_ascii=False) + ';\n'
js_content += 'window.TABLE_DATA = ' + json.dumps(table_records, ensure_ascii=False) + ';\n'
js_content += 'window.YEARS = ' + json.dumps(YEARS) + ';\n'

with open(out_js, 'w', encoding='utf-8') as f:
    f.write(js_content)

print(f'\n数据文件已生成: {out_js}')
print(f'  地图数据: 省{len(map_data["province"])} 市{len(map_data["city"])} 区{len(map_data["district"])}')
print(f'  表格数据: {len(table_records)} 条职位')
