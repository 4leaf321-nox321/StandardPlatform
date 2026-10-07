@echo off
rem 정제 도구 키트 점검 — 설치했는데 Claude Desktop 에 sp-pipeline 이 안 뜰 때 더블클릭한다.
rem 아무것도 바꾸지 않는다. 나온 화면을 그대로 복사해 물어보면 된다(토큰은 가려져 있다).
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PY="
if exist "%~dp0python\python.exe" set PY="%~dp0python\python.exe"
if not defined PY ( py -3 -c "import sys" >nul 2>nul && set "PY=py -3" )
if not defined PY ( python -c "import sys" >nul 2>nul && set "PY=python" )
if not defined PY goto nopython

%PY% "%~dp0sp_setup.py" --check
pause
exit /b 0

:nopython
echo 키트의 python 폴더가 없습니다 — 「압축 풀기」 로 푼 폴더에서 다시 더블클릭하세요.
pause
exit /b 1
