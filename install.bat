@echo off
REM ===========================================================================
REM  MKCREW one-click installer - double-click this file.
REM  Launches install.ps1 with the execution policy bypassed for THIS run only
REM  (it does not change your system policy). User-scope; no admin needed.
REM ===========================================================================
chcp 65001 >nul
title MKCREW - bootstrap ^& preflight
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
