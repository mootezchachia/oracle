#!/bin/bash
# ORACLE Daemon — starts on boot, auto-restarts on crash
# Install: add to crontab with @reboot, or source from .bashrc/.profile
#
# Usage:
#   ./oracle-daemon.sh start   — start in background
#   ./oracle-daemon.sh stop    — stop gracefully
#   ./oracle-daemon.sh status  — check if running
#   ./oracle-daemon.sh logs    — tail live logs

set -e
cd "$(dirname "$0")"

PIDFILE="nerve/data/oracle.pid"
LOGFILE="nerve/data/oracle.log"

case "${1:-start}" in
  start)
    # Check if already running
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "ORACLE already running (PID $(cat "$PIDFILE"))"
      exit 0
    fi

    echo "Starting ORACLE daemon..."

    # Run with auto-restart loop
    (
      while true; do
        echo "[$(date)] Starting server.py..." >> "$LOGFILE"
        python3 server.py >> "$LOGFILE" 2>&1
        EXIT_CODE=$?
        echo "[$(date)] Server exited with code $EXIT_CODE. Restarting in 10s..." >> "$LOGFILE"
        sleep 10
      done
    ) &

    echo $! > "$PIDFILE"
    echo "ORACLE started (PID $!)"
    echo "Dashboard: http://localhost:3000"
    echo "Logs: tail -f $LOGFILE"
    ;;

  stop)
    if [ -f "$PIDFILE" ]; then
      PID=$(cat "$PIDFILE")
      echo "Stopping ORACLE (PID $PID)..."
      kill "$PID" 2>/dev/null || true
      # Also kill any child python processes
      pkill -f "python3 server.py" 2>/dev/null || true
      rm -f "$PIDFILE"
      echo "Stopped."
    else
      echo "ORACLE not running (no pidfile)"
    fi
    ;;

  restart)
    $0 stop
    sleep 2
    $0 start
    ;;

  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "ORACLE running (PID $(cat "$PIDFILE"))"
      # Show last strategy run
      tail -5 "$LOGFILE" 2>/dev/null
    else
      echo "ORACLE not running"
    fi
    ;;

  logs)
    tail -f "$LOGFILE"
    ;;

  *)
    echo "Usage: ./oracle-daemon.sh {start|stop|restart|status|logs}"
    ;;
esac
