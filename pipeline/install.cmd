@echo off
rem StandardPlatform 정제 도구 키트 — 더블클릭으로 설치 · 플랫폼 등록.
rem 플랫폼 화면 「내 정보」 의 「이 PC 에 등록 정보 복사」 를 누른 뒤 실행한다.
rem 처음이면 설치까지, 이미 설치했으면 그 플랫폼을 더한다(앞의 것은 남는다).
chcp 65001 >nul
cd /d "%~dp0"
rem UTF-8 모드 — 한국어 Windows 의 파이썬은 파일을 cp949 로 읽는다(실측).
set "PYTHONUTF8=1"

rem 키트에 든 파이썬(python\) — PC 에 파이썬이 없어도, 어느 판이 있어도 이것으로 돈다.
rem 전역 설치가 아니다(레지스트리 · PATH 를 안 건드린다). 없으면(저장소에서 바로 쓸 때)
rem PC 의 파이썬 — py 런처(3.12 우선) → python.
set "PY="
if exist "%~dp0python\python.exe" set PY="%~dp0python\python.exe"
if not defined PY ( py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12" )
if not defined PY ( py -3 -c "import sys" >nul 2>nul && set "PY=py -3" )
if not defined PY ( python -c "import sys" >nul 2>nul && set "PY=python" )
if not defined PY goto nopython

%PY% "%~dp0sp_setup.py" --from-clipboard --write-claude %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" echo 끝났습니다. 이제 Claude Desktop 을 켜세요. 안 보이면 같은 폴더의 check.cmd 를 더블클릭하세요.
pause
exit /b %CODE%

:nopython
echo 키트의 python 폴더가 없습니다.
echo zip 안에서 바로 실행하면 이렇게 됩니다 — 「압축 풀기」 로 푼 폴더에서 다시 더블클릭하세요.
pause
exit /b 1
