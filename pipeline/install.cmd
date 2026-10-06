@echo off
rem StandardPlatform 정제 도구 키트 — 더블클릭으로 설치 · 플랫폼 등록.
rem 플랫폼 화면 「내 정보」 의 「이 PC 에 등록 정보 복사」 를 누른 뒤 실행한다.
rem 처음이면 설치까지, 이미 설치했으면 그 플랫폼을 더한다(앞의 것은 남는다).
chcp 65001 >nul
cd /d "%~dp0"
rem UTF-8 모드 — 한국어 Windows 의 파이썬은 파일을 cp949 로 읽어 pip 가 죽는다(실측).
set "PYTHONUTF8=1"

rem 파이썬을 찾는다 — py 런처(3.12 우선) → python. Microsoft Store 의 가짜 python 은 -c 가 실패해 걸러진다.
set "PY="
py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY ( py -3 -c "import sys" >nul 2>nul && set "PY=py -3" )
if not defined PY ( python -c "import sys" >nul 2>nul && set "PY=python" )
if not defined PY goto nopython

%PY% "%~dp0sp_setup.py" --from-clipboard --write-claude %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" echo 끝났습니다. Claude Desktop 을 완전히 종료(작업 표시줄 아이콘 → 종료)했다가 다시 켜세요.
pause
exit /b %CODE%

:nopython
echo 파이썬이 없습니다. python.org 에서 Python 3.12 를 설치하세요.
echo 설치 첫 화면의 "Add python.exe to PATH" 를 꼭 체크한 뒤, 이 파일을 다시 더블클릭하세요.
pause
exit /b 1
