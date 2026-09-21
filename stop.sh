#!/data/data/com.termux/files/usr/bin/bash

PREFIX="/data/data/com.termux/files/usr"
HOME_DIR="/data/data/com.termux/files/home"
LOG_DIR="$HOME_DIR/.brown-gp/logs"

PGREP="$PREFIX/bin/pgrep"
PKILL="$PREFIX/bin/pkill"
WAKE_UNLOCK="$PREFIX/bin/termux-wake-unlock"

STOP_LOG="$LOG_DIR/stop.log"

mkdir -p "$LOG_DIR"

log() {
    message="$(date '+%Y-%m-%d %H:%M:%S') $1"
    echo "$message"
    echo "$message" >> "$STOP_LOG"
}

log "Brown GP shutdown requested"

if "$PGREP" -f 'cloudflared tunnel run f1-server' >/dev/null 2>&1; then
    log "Stopping Cloudflare tunnel"
    "$PKILL" -f 'cloudflared tunnel run f1-server'
    sleep 2

    if "$PGREP" -f 'cloudflared tunnel run f1-server' >/dev/null 2>&1; then
        log "ERROR: Cloudflare tunnel is still running"
    else
        log "Cloudflare tunnel stopped"
    fi
else
    log "Cloudflare tunnel already stopped"
fi

if "$PGREP" -f 'python main\.py' >/dev/null 2>&1; then
    log "Stopping Brown GP backend"
    "$PKILL" -f 'python main\.py'
    sleep 2

    if "$PGREP" -f 'python main\.py' >/dev/null 2>&1; then
        log "ERROR: Brown GP backend is still running"
    else
        log "Brown GP backend stopped"
    fi
else
    log "Brown GP backend already stopped"
fi

if "$PGREP" -f '/data/data/com\.termux/files/usr/bin/sshd' >/dev/null 2>&1; then
    log "Stopping SSH"
    "$PKILL" -f '/data/data/com\.termux/files/usr/bin/sshd'
    sleep 1

    if "$PGREP" -f '/data/data/com\.termux/files/usr/bin/sshd' >/dev/null 2>&1; then
        log "ERROR: SSH is still running"
    else
        log "SSH stopped"
    fi
else
    log "SSH already stopped"
fi

if "$WAKE_UNLOCK" >/dev/null 2>&1; then
    log "Wake lock released"
else
    log "WARNING: unable to release Termux wake lock"
fi

log "Brown GP shutdown sequence complete"
