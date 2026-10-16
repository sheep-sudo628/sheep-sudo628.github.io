# -*- coding: utf-8 -*-
"""
本机抓取后一键发布到 GitHub Pages（sheep-sudo628.github.io）
流程: 站点目录 pull -> 同步项目文件 -> index.html=主看板 -> 清理历史快照 -> commit -> push(重试)
说明:
  - 云端 GitHub Actions 每2小时更新华图数据；本机数据是中公+华图全量超集,
    rebase 冲突时一律以本机版本为准(-X ours)。
  - 由 deploy_pages.bat 调用, 也可直接 python deploy_pages.py 手动发布。
"""
import glob
import os
import shutil
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, '.gh-pages-site')
GIT = r'C:\Program Files\Git\cmd\git.exe'
if not os.path.exists(GIT):
    GIT = 'git'

FILES = [
    '2027国考报名实时看板.html', '国考报名实时数据.html', '国考数据可视化报告.html',
    '国考数据分析报告.xlsx',
    'bmrs27_data.js', 'bmrs_data.js', 'report_data.js', 'echarts.min.js',
    'scrape_2027.py', 'scrape_bmrs.py', 'build_report.py', 'process_data.py',
    'run_scrape.bat', 'deploy_pages.bat', 'deploy_pages.py', '.gitignore',
]
DIRS = ['data']


def git(args):
    return subprocess.run([GIT, '-C', SITE] + args, capture_output=True, text=True)


def keep_latest(pattern, keep=1):
    files = sorted(glob.glob(os.path.join(SITE, pattern)),
                   key=os.path.getmtime, reverse=True)
    for f in files[keep:]:
        os.remove(f)


def main():
    if not os.path.isdir(os.path.join(SITE, '.git')):
        sys.exit('站点目录不存在，请先执行:\n'
                 f'  git clone https://github.com/sheep-sudo628/sheep-sudo628.github.io.git '
                 f'"{SITE}"')

    print('[1/5] 拉取云端最新版本（含 GitHub Actions 的自动更新）', flush=True)
    git(['stash', 'push'])
    r = git(['pull', '--rebase', '-X', 'ours', 'origin', 'main'])
    sys.stdout.write(r.stdout)
    git(['stash', 'drop'])

    print('[2/5] 同步项目文件', flush=True)
    for name in FILES:
        src = os.path.join(ROOT, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(SITE, name))
    for d in DIRS:
        dst = os.path.join(SITE, d)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(os.path.join(ROOT, d), dst)

    print('[3/5] 同步 index.html 根入口；历史快照只保留最新一份', flush=True)
    shutil.copy2(os.path.join(ROOT, '2027国考报名实时看板.html'),
                 os.path.join(SITE, 'index.html'))
    keep_latest(os.path.join('data', 'bmrs27', 'snap_*.json'))
    keep_latest(os.path.join('data', 'bmrs', '*.json.gz'))

    print('[4/5] 提交', flush=True)
    git(['add', '-A'])
    if git(['diff', '--cached', '--quiet']).returncode == 0:
        print('无变更，跳过推送', flush=True)
        return
    msg = '本机自动更新：%s 抓取数据' % datetime.now().strftime('%Y-%m-%d %H:%M')
    r = git(['commit', '-m', msg])
    sys.stdout.write(r.stdout)

    print('[5/5] 推送（失败自动拉取重试，最多3次）', flush=True)
    for i in range(3):
        r = git(['push', 'origin', 'main'])
        if r.returncode == 0:
            print('发布成功 -> https://sheep-sudo628.github.io/', flush=True)
            return
        sys.stdout.write(r.stderr)
        git(['pull', '--rebase', '-X', 'ours', 'origin', 'main'])
    sys.exit('推送失败，请检查网络后重跑 deploy_pages.bat')


if __name__ == '__main__':
    main()
