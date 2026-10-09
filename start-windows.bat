@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [보스 캐릭터 관리] 로컬 도우미를 실행합니다 (http://localhost:8787)
echo 이 창은 열어 두세요. 이미 열어 둔 index.html 페이지에서 "결정석 가격 갱신"/API 버튼을 그대로 누르면 됩니다.
echo 브라우저에서 바로 열고 싶다면: python serve.py --open
where python >nul 2>nul && (python serve.py %*) || (py serve.py %*)
pause
