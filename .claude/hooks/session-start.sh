#!/bin/bash
set -euo pipefail

# Only run in remote (web) environment
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install Node.js dependencies for the dashboard
cd "$CLAUDE_PROJECT_DIR/oracle-dashboard"
npm install 2>/dev/null || true

# Install Python dependencies for API
cd "$CLAUDE_PROJECT_DIR"
if command -v pip &>/dev/null; then
  pip install -r requirements.txt 2>/dev/null || true
fi

# Load environment variables for the session
if [ -f "$CLAUDE_PROJECT_DIR/.env" ]; then
  echo "export \$(grep -v '^#' $CLAUDE_PROJECT_DIR/.env | xargs)" >> "$CLAUDE_ENV_FILE"
fi

# Fetch latest ORACLE brief on session start
echo ""
echo "=== ORACLE STATUS ==="
BRIEF=$(curl -s --max-time 10 "https://oracle-psi-orpin.vercel.app/api/strategy100-run?action=ai-brief" 2>/dev/null || echo '{}')
echo "$BRIEF" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    brief = d.get('brief', '')
    if brief:
        print(brief[:500] if isinstance(brief, str) else json.dumps(brief)[:500])
    else:
        print('No AI brief cached. Run /oracle for full analysis.')
except:
    print('ORACLE API unreachable. Run /oracle for analysis.')
" 2>/dev/null || echo "Run /oracle for full analysis."
echo "===================="
