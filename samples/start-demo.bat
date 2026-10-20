@echo off
rem Starts the FleetOps demo (separate demo database). Extra arguments are passed on, e.g. -Users
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" %*
