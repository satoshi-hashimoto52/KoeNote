#!/bin/bash
# KoeNote を開発モードで起動する（Vite + Electron。Backend は Electron が自動起動）。
set -e
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -d node_modules ]; then
  echo "node_modules がありません。npm install を実行します..."
  npm install
fi

if [ ! -d .venv ]; then
  echo ".venv がありません。README の手順で Python 環境を用意してください。"
fi

npm run dev
