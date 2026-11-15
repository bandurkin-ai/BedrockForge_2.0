@echo off
rem BedrockForge: update from GitHub, rebuild and restart api + worker, keep a log.
rem The log is written next to this file: BedrockForge_log.txt (the previous run: BedrockForge_log_prev.txt).
rem Messages are ASCII on purpose: cmd.exe garbles other characters depending on the code page.

setlocal
set "REPO=c:\Users\bandu\BedrockForge"
set "BRANCH=base-functional"
set "LOG=%~dp0BedrockForge_log.txt"

if exist "%LOG%" move /y "%LOG%" "%~dp0BedrockForge_log_prev.txt" >nul
>"%LOG%" echo === BedrockForge run %date% %time% ===
>>"%LOG%" echo repo: %REPO%   branch: %BRANCH%
echo Log: %LOG%

cd /d "%REPO%" || (
  echo ERROR: folder not found: %REPO%
  >>"%LOG%" echo ERROR: folder not found: %REPO%
  goto :failed
)

call :run "docker info" || (
  echo ERROR: Docker is not running. Start Docker Desktop and run this file again.
  >>"%LOG%" echo ERROR: Docker is not running.
  goto :failed
)
call :run "git checkout %BRANCH%" || goto :failed
call :run "git pull origin %BRANCH%" || goto :failed
call :run "git log --oneline -3" || goto :failed
call :run "docker compose up --build -d" || goto :failed
rem The web container serves the sources from the folder; restart it so it surely picks up the update.
call :run "docker compose restart web" || goto :failed

rem Give api (migrations) and worker a few seconds to start, then record their state.
timeout /t 10 /nobreak >nul
call :run "docker compose ps"
call :run "docker compose logs --tail 60 api worker"

>>"%LOG%" echo.
>>"%LOG%" echo === OK %date% %time% ===
echo.
echo DONE. Open http://localhost:5173 and press Ctrl+F5 (the page is ready about 20 seconds after the restart).
echo Log: %LOG%
goto :end

:failed
rem Even after an error, record what the containers say (when Docker is up).
docker compose ps >>"%LOG%" 2>&1
docker compose logs --tail 80 api worker >>"%LOG%" 2>&1
>>"%LOG%" echo.
>>"%LOG%" echo === FAILED %date% %time% ===
echo.
echo FAILED. See the log: %LOG%
start notepad "%LOG%"

:end
echo.
pause
endlocal
exit /b 0

:run
rem Runs a command, shows its name, appends its output and exit code to the log.
echo.
echo ^> %~1
>>"%LOG%" echo.
>>"%LOG%" echo ^> %~1
cmd /c %~1 >>"%LOG%" 2>&1
set "RC=%errorlevel%"
>>"%LOG%" echo [exit code %RC%]
exit /b %RC%
