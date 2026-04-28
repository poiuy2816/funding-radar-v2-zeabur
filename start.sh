#!/bin/sh
set -e

mkdir -p /app/data

echo "Starting Funding Radar worker..."
python /app/main.py &

echo "Starting Streamlit UI..."
streamlit run /app/ui.py \
  --server.address=0.0.0.0 \
  --server.port=${PORT:-8501} \
  --server.headless=true \
  --browser.gatherUsageStats=false
