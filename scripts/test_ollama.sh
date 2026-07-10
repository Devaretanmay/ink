#!/bin/bash

# Ensure dependencies in a virtualenv
echo "Setting up virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
pip install requests > /dev/null 2>&1

echo "Pre-compiling binary..."
cargo build -p microloop-proxy

echo "Starting Proxy (port 8080) pointing to Ollama..."
TARGET_API_URL=http://127.0.0.1:11434/v1 cargo run -p microloop-proxy > proxy_ollama.log 2>&1 &
PROXY_PID=$!

# Wait for it to start up
echo "Waiting 5 seconds for services to start..."
sleep 5

echo "Running Ollama Agent Test..."
python3 python/test_ollama.py

echo "--- PROXY LOGS ---"
cat proxy_ollama.log

# Cleanup
echo "Killing background processes..."
kill $PROXY_PID
