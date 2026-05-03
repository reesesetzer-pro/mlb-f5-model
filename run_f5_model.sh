#!/bin/bash
# MLB F5 Model — Daily Sync
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="/Users/reesesetzer/Desktop/BettingModels/.venv/bin/python"
LOG="$DIR/master_sync_log.txt"

echo
echo "============================================================"
echo " MLB F5 MODEL — DAILY SYNC"
echo " $(date)"
echo "============================================================"
echo

echo "[$(date)] Master sync started" >> "$LOG"

echo "[1/2] Running data sync (SP stats, lineups, park factors, umpires)..."
echo
if ! "$PYTHON" "$DIR/data_sync.py"; then
    echo
    echo "ERROR: data_sync.py failed. Check data_sync_log.txt"
    echo "[$(date)] ERROR: data_sync.py failed" >> "$LOG"
    exit 1
fi

echo
echo "Waiting 10 seconds before odds pull..."
sleep 10

echo
echo "[2/2] Running odds sync (F5 lines from The Odds API)..."
echo
if ! "$PYTHON" "$DIR/f5_sync.py"; then
    echo
    echo "ERROR: f5_sync.py failed. Check MLB_F5_Model_sync_log.txt"
    echo "[$(date)] ERROR: f5_sync.py failed" >> "$LOG"
    exit 1
fi

echo
echo "============================================================"
echo " ALL DONE — Excel is ready to open"
echo " $(date)"
echo "============================================================"
echo
echo "[$(date)] Master sync completed successfully" >> "$LOG"
