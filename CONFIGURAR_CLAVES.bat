@echo off
chcp 65001 >nul
title Configurar claves del bot
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\iniciar.ps1" -SoloConfigurar
pause
