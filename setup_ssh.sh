#!/usr/bin/env bash
# setup_ssh.sh — Generate and install SSH key for hydra → sulley
#
# Run this once on hydra as the user who will run run_ctrl.
# It will:
#   1. Generate a dedicated ed25519 key pair
#   2. Copy the public key to sulley (you'll be prompted for sulley password once)
#   3. Test the connection
#
# Usage:
#   bash setup_ssh.sh [sulley_user] [sulley_host]
#
# Defaults:
#   sulley_user = tng
#   sulley_host = sulley

set -euo pipefail

SULLEY_USER="${1:-tng}"
SULLEY_HOST="${2:-sulley}"
KEY_PATH="$HOME/.ssh/id_ed25519_sulley"

echo ""
echo "── SSH Key Setup: hydra → sulley ────────────────────────"
echo "  Key   : $KEY_PATH"
echo "  Target: $SULLEY_USER@$SULLEY_HOST"
echo "──────────────────────────────────────────────────────────"
echo ""

# 1. Generate key (skip if already exists)
if [ -f "$KEY_PATH" ]; then
    echo "✓ Key already exists at $KEY_PATH — skipping generation."
else
    echo "Generating ed25519 key pair..."
    ssh-keygen -t ed25519 -f "$KEY_PATH" -C "run_ctrl@hydra" -N ""
    echo "✓ Key generated."
fi

# 2. Copy public key to sulley
echo ""
echo "Installing public key on $SULLEY_HOST (you'll be prompted for sulley password)..."
ssh-copy-id -i "${KEY_PATH}.pub" "${SULLEY_USER}@${SULLEY_HOST}"
echo "✓ Public key installed on sulley."

# 3. Test
echo ""
echo "Testing connection..."
if ssh -i "$KEY_PATH" -o BatchMode=yes -o ConnectTimeout=5 \
       "${SULLEY_USER}@${SULLEY_HOST}" "echo 'Connection OK'"; then
    echo ""
    echo "✓ SSH key-based auth working."
    echo ""
    echo "Your run_config.yaml should have:"
    echo "  general:"
    echo "    daq_server:"
    echo "      host: ${SULLEY_HOST}"
    echo "      user: ${SULLEY_USER}"
    echo "    ssh_key: ${KEY_PATH}"
else
    echo ""
    echo "✗ Connection test failed. Check sulley hostname and user."
    exit 1
fi
