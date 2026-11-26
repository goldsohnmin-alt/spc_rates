@echo off
rem PC fill run for Task Scheduler. Log: output\pc_fill.log
cd /d C:\dev\spc_rates
if not exist output mkdir output
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -u pc_fill.py >> output\pc_fill.log 2>&1
