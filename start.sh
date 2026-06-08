#!/bin/bash
# 本地测试启动脚本（MyAgents 集成不需要此文件）
export CASE_DB_PATH="${CASE_DB_PATH:-$HOME/.myagents/case_db}"
exec uv run python server.py
