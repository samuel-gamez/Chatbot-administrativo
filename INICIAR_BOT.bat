@echo off
chcp 65001 >nul
title Bot de admisiones - NO CERRAR esta ventana
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\iniciar.ps1"
pause
