#!/bin/bash
cd "$(dirname "$0")"
echo "Installing dependencies..."
pip3 install -r requirements.txt -q
echo ""
echo "Starting Put Screener..."
echo "Open your browser to: http://localhost:5050"
echo "Press Ctrl+C to stop."
echo ""
python3 app.py
