@echo off
REM Publish latest scraped data to GitHub Pages after local scrape
setlocal
set "PY=C:\Users\61402\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"
cd /d d:\py\list
if not exist "data\bmrs" mkdir "data\bmrs"
"%PY%" deploy_pages.py >> "data\bmrs\scrape.log" 2>&1
