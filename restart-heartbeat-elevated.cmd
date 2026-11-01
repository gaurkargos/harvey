@echo off
REM Double-click this to restart the Harvey heartbeat with the code from the last commit.
REM It shows one UAC prompt (Windows needs an admin token to kill the session-0 loop).
REM Scope: kills only harvey.exe, then starts the "Harvey Heartbeat" scheduled task.
REM Log: C:\Users\gaurk\harvey\kill-stale-heartbeat.log
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\gaurk\harvey\scripts\kill-stale-heartbeat.ps1')"
