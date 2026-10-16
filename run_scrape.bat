@echo off
REM National civil-service registration data - scheduled scrape entry
setlocal
set "PY=C:\Users\61402\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"
cd /d d:\py\list
if not exist "data\bmrs" mkdir "data\bmrs"
echo [%date% %time%] === 2027 daily start === >> "data\bmrs\scrape.log"
"%PY%" scrape_2027.py >> "data\bmrs\scrape.log" 2>&1
echo [%date% %time%] === 2027 daily exit=%errorlevel% === >> "data\bmrs\scrape.log"
echo [%date% %time%] === rank full start === >> "data\bmrs\scrape.log"
"%PY%" scrape_bmrs.py >> "data\bmrs\scrape.log" 2>&1
echo [%date% %time%] === rank full exit=%errorlevel% === >> "data\bmrs\scrape.log"
echo [%date% %time%] === deploy pages start === >> "data\bmrs\scrape.log"
call "%~dp0deploy_pages.bat"
echo [%date% %time%] === deploy pages exit=%errorlevel% === >> "data\bmrs\scrape.log"
