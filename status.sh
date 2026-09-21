#!/data/data/com.termux/files/usr/bin/bash

PREFIX="/data/data/com.termux/files/usr"
PGREP="$PREFIX/bin/pgrep"

echo "Brown GP deployment status"
echo "=========================="

if "$PGREP" -f 'python main\.py' >/dev/null 2>&1; then
    echo "Backend:     RUNNING"
    "$PGREP" -af 'python main\.py'
else
    echo "Backend:     STOPPED"
fi

if "$PGREP" -f 'cloudflared tunnel run f1-server' >/dev/null 2>&1; then
    echo "Cloudflare:  RUNNING"
    "$PGREP" -af 'cloudflared tunnel run f1-server'
else
    echo "Cloudflare:  STOPPED"
fi

if "$PGREP" -f '/data/data/com\.termux/files/usr/bin/sshd' >/dev/null 2>&1; then
    echo "SSH:         RUNNING"
    "$PGREP" -af '/data/data/com\.termux/files/usr/bin/sshd'
else
    echo "SSH:         STOPPED"
fi
