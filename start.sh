#!/data/data/com.termux/files/usr/bin/bash

PREFIX="/data/data/com.termux/files/usr"
HOME_DIR="/data/data/com.termux/files/home"
PROJECT_DIR="$HOME_DIR/brown-gp"
BACKEND_DIR="$PROJECT_DIR/backend"
LOG_DIR="$HOME_DIR/.brown-gp/logs"

PYTHON="$PREFIX/bin/python"
CLOUDFLARED="$PREFIX/bin/cloudflared"
SSHD="$PREFIX/bin/sshd"
WAKE_LOCK="$PREFIX/bin/termux-wake-lock"
PGREP="$PREFIX/bin/pgrep"

BACKEND_LOG="$LOG_DIR/backend.log"
CLOUDFLARED_LOG="$LOG_DIR/cloudflared.log"
SSHD_LOG="$LOG_DIR/sshd.log"
START_LOG="$LOG_DIR/start.log"

mkdir -p "$LOG_DIR"

log() {
    message="$(date '+%Y-%m-%d %H:%M:%S') $1"
    echo "$message"
    echo "$message" >> "$START_LOG"
}

log "Brown GP startup requested"

if "$WAKE_LOCK" >/dev/null 2>&1; then
    log "Wake lock acquired"
else
    log "WARNING: unable to acquire Termux wake lock"
fi

if "$PGREP" -f '/data/data/com\.termux/files/usr/bin/sshd' >/dev/null 2>&1; then
    log "SSH already running"
else
    log "Starting SSH"
    "$SSHD" >> "$SSHD_LOG" 2>&1
    sleep 1

    if "$PGREP" -f '/data/data/com\.termux/files/usr/bin/sshd' >/dev/null 2>&1; then
        log "SSH started"
    else
        log "ERROR: SSH did not start"
    fi
fi

if "$PGREP" -f 'python main\.py' >/dev/null 2>&1; then
    log "Brown GP backend already running"
else
    log "Starting Brown GP backend"

    cd "$BACKEND_DIR" || {
        log "ERROR: cannot enter backend directory: $BACKEND_DIR"
        exit 1
    }

    nohup "$PYTHON" main.py >> "$BACKEND_LOG" 2>&1 &
    sleep 3

    if "$PGREP" -f 'python main\.py' >/dev/null 2>&1; then
        log "Brown GP backend started"
    else
        log "ERROR: Brown GP backend did not start"
    fi
fi

if "$PGREP" -f 'cloudflared tunnel run f1-server' >/dev/null 2>&1; then
    log "Cloudflare tunnel already running"
else
    log "Starting Cloudflare tunnel f1-server"

    cd "$HOME_DIR" || {
        log "ERROR: cannot enter home directory: $HOME_DIR"
        exit 1
    }

    nohup "$CLOUDFLARED" tunnel run f1-server >> "$CLOUDFLARED_LOG" 2>&1 &
    sleep 3

    if "$PGREP" -f 'cloudflared tunnel run f1-server' >/dev/null 2>&1; then
        log "Cloudflare tunnel started"
    else
        log "ERROR: Cloudflare tunnel did not start"
    fi
fi

log "Brown GP startup sequence complete"
