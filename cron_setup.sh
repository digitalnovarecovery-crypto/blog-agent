#!/bin/bash
# =============================================================================
# Eudaimonia Blog Agent — Cron Setup Script
# Run this on the GoDaddy VPS to set up automated pipeline execution.
#
# Usage:
#   chmod +x cron_setup.sh
#   ./cron_setup.sh
# =============================================================================

set -e

# -- Configuration --
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_DIR="${AGENT_DIR}/logs"
CRON_SCHEDULE="${CRON_SCHEDULE:-0 */4 * * *}"  # Every 4 hours by default

echo "============================================="
echo "Eudaimonia Blog Agent — Cron Setup"
echo "============================================="
echo "Agent directory: ${AGENT_DIR}"
echo "Python binary:   ${PYTHON_BIN}"
echo "Cron schedule:   ${CRON_SCHEDULE}"
echo ""

# -- Step 1: Verify Python --
echo "[1/5] Checking Python..."
if ! command -v ${PYTHON_BIN} &> /dev/null; then
    echo "ERROR: ${PYTHON_BIN} not found. Install Python 3.10+ or set PYTHON_BIN."
    exit 1
fi
PYTHON_VERSION=$(${PYTHON_BIN} --version 2>&1)
echo "  Found: ${PYTHON_VERSION}"

# -- Step 2: Verify dependencies --
echo "[2/5] Checking dependencies..."
${PYTHON_BIN} -c "
import sys
missing = []
for mod in ['anthropic', 'requests', 'dotenv', 'flask', 'ringcentral']:
    try:
        __import__(mod)
    except ImportError:
        missing.append(mod)
if missing:
    print(f'  Missing: {missing}')
    print('  Run: pip install ringcentral requests python-dotenv flask anthropic authlib gunicorn')
    sys.exit(1)
print('  All dependencies OK.')
"

# -- Step 3: Verify .env exists --
echo "[3/5] Checking .env..."
if [ ! -f "${AGENT_DIR}/.env" ]; then
    echo "ERROR: .env file not found at ${AGENT_DIR}/.env"
    echo "  Copy .env.example and fill in your credentials."
    exit 1
fi
echo "  .env found."

# -- Step 4: Create logs directory --
echo "[4/5] Setting up logs directory..."
mkdir -p "${LOG_DIR}"
echo "  Created: ${LOG_DIR}"

# -- Step 5: Install cron job --
echo "[5/5] Installing cron job..."

CRON_CMD="cd ${AGENT_DIR} && ${PYTHON_BIN} pipeline_runner.py >> ${LOG_DIR}/pipeline.log 2>&1"

# Check if cron entry already exists
if crontab -l 2>/dev/null | grep -qF "pipeline_runner.py"; then
    echo "  WARNING: Existing cron entry found for pipeline_runner.py."
    echo "  Current crontab entries:"
    crontab -l 2>/dev/null | grep "pipeline_runner" || true
    echo ""
    read -p "  Replace existing entry? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Remove old entry and add new one
        (crontab -l 2>/dev/null | grep -vF "pipeline_runner.py"; echo "${CRON_SCHEDULE} ${CRON_CMD}") | crontab -
        echo "  Cron entry replaced."
    else
        echo "  Keeping existing entry."
    fi
else
    # Add new entry
    (crontab -l 2>/dev/null; echo "${CRON_SCHEDULE} ${CRON_CMD}") | crontab -
    echo "  Cron entry added."
fi

echo ""
echo "============================================="
echo "Setup complete!"
echo "============================================="
echo ""
echo "The pipeline will run on this schedule: ${CRON_SCHEDULE}"
echo "  (default: every 4 hours at :00)"
echo ""
echo "Logs will be written to: ${LOG_DIR}/pipeline.log"
echo ""
echo "To test manually:"
echo "  cd ${AGENT_DIR}"
echo "  ${PYTHON_BIN} pipeline_runner.py --dry-run"
echo ""
echo "To test with a single site and one post:"
echo "  ${PYTHON_BIN} pipeline_runner.py --site eudaimonia --max-posts 1"
echo ""
echo "To view current cron entries:"
echo "  crontab -l"
echo ""
echo "To view pipeline logs:"
echo "  tail -f ${LOG_DIR}/pipeline.log"
echo ""
