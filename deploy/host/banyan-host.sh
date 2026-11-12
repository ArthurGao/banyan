#!/usr/bin/env bash
# Banyan on the host with the Claude subscription (owner decision 2026-11-13; deploy/README.md §8).
#
#   deploy/host/banyan-host.sh up [--build]   start Ollama, llm-gateway, llm-steward, Banyan
#   deploy/host/banyan-host.sh down           stop all four
#   deploy/host/banyan-host.sh status         health of each
#   deploy/host/banyan-host.sh logs [name]    follow a log (gateway | steward | banyan)
#
# Nothing starts at login: the launchd jobs live outside ~/Library/LaunchAgents and are loaded only by
# `up` (they then restart on a crash until `down` or logout). Ollama keeps its machine rule: started
# with `brew services run`, never `start` (KB global/machine/local-services.md § Ollama).
# Configuration comes from the services' own files: llm-gateway's config, llm-steward's .env and
# Banyan's backend/.env (roles, steward keys, search key). This script prints no secret.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
GATEWAY_DIR="${BANYAN_GATEWAY_DIR:-$HOME/Code/AI/llm-gateway}"
STEWARD_DIR="${BANYAN_STEWARD_DIR:-$HOME/Code/AI/llm-steward/backend}"
JOBS_DIR="$HOME/.local/share/banyan/launchd"
LOGS="$HOME/Library/Logs/banyan"
DOMAIN="gui/$(id -u)"
UV="$(command -v uv)"
PATH_FOR_JOBS="$(dirname "$UV"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# name|label|port|health path
SERVICES=(
  "gateway|dev.banyan.llm-gateway|8787|/healthz"
  "steward|dev.banyan.llm-steward|8600|/health"
  "banyan|dev.banyan.api|8800|/health"
)

field() { cut -d'|' -f"$2" <<<"$1"; }

healthy() { curl -fs -o /dev/null --max-time 2 "http://127.0.0.1:$1$2"; }

wait_healthy() {  # name port path seconds
  local i
  for ((i = 0; i < $4; i++)); do
    if healthy "$2" "$3"; then echo "  $1 ok (:$2)"; return 0; fi
    sleep 1
  done
  echo "  $1 did not become healthy on :$2 — see $LOGS/$1.log" >&2
  return 1
}

# Who holds a port, unless it is our own job.
foreign_listener() {  # port label
  local pid
  pid="$(lsof -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null | head -1 || true)"
  [[ -z "$pid" ]] && return 1
  if launchctl print "$DOMAIN/$2" 2>/dev/null | grep -q "pid = $pid"; then return 1; fi
  ps -o pid=,command= -p "$pid" | cut -c1-120
}

write_job() {  # label log program-args...
  local label="$1" log="$2"
  shift 2
  local args=""
  for a in "$@"; do args+="    <string>${a//&/&amp;}</string>"$'\n'; done
  cat >"$JOBS_DIR/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
$args  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$PATH_FOR_JOBS</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$log</string>
  <key>StandardErrorPath</key><string>$log</string>
</dict>
</plist>
PLIST
}

write_jobs() {
  mkdir -p "$JOBS_DIR" "$LOGS"
  write_job dev.banyan.llm-gateway "$LOGS/gateway.log" \
    /bin/bash -c "cd '$GATEWAY_DIR' && exec '$UV' run llmgw serve"
  write_job dev.banyan.llm-steward "$LOGS/steward.log" \
    /bin/bash "$REPO/deploy/host/run-steward.sh" "$STEWARD_DIR" "$UV"
  write_job dev.banyan.api "$LOGS/banyan.log" \
    /bin/bash -c "cd '$REPO/backend' && '$UV' run banyan migrate && BANYAN_FRONTEND_DIST='$REPO/frontend/dist' exec '$UV' run banyan serve"
}

up() {
  local build=0
  [[ "${1:-}" == "--build" ]] && build=1
  local s name label port blocked=0 who
  for s in "${SERVICES[@]}"; do
    name="$(field "$s" 1)"; label="$(field "$s" 2)"; port="$(field "$s" 3)"
    if who="$(foreign_listener "$port" "$label")"; then
      echo "port $port ($name) is held by another process, not starting over it:" >&2
      echo "  $who" >&2
      blocked=1
    fi
  done
  ((blocked)) && exit 1

  if ((build)) || [[ ! -f "$REPO/frontend/dist/index.html" ]]; then
    echo "building the frontend…"
    (cd "$REPO/frontend" && pnpm install --frozen-lockfile >/dev/null && pnpm build >/dev/null)
  fi

  echo "Ollama"
  healthy 11434 /api/version || brew services run ollama >/dev/null
  wait_healthy ollama 11434 /api/version 30

  write_jobs
  for s in "${SERVICES[@]}"; do
    name="$(field "$s" 1)"; label="$(field "$s" 2)"
    launchctl print "$DOMAIN/$label" >/dev/null 2>&1 || launchctl bootstrap "$DOMAIN" "$JOBS_DIR/$label.plist"
    wait_healthy "$name" "$(field "$s" 3)" "$(field "$s" 4)" 90
  done
  echo "Banyan: http://127.0.0.1:8800  (logs: $LOGS)"
}

down() {
  local s label
  for s in "${SERVICES[@]}"; do
    label="$(field "$s" 2)"
    if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
      launchctl bootout "$DOMAIN/$label" && echo "stopped $(field "$s" 1)"
    fi
  done
  brew services stop ollama >/dev/null 2>&1 && echo "stopped Ollama" || true
}

status() {
  local s
  printf '%-8s %s\n' ollama "$(healthy 11434 /api/version && echo ok || echo down)"
  for s in "${SERVICES[@]}"; do
    printf '%-8s %s\n' "$(field "$s" 1)" "$(healthy "$(field "$s" 3)" "$(field "$s" 4)" && echo ok || echo down)"
  done
  if healthy 8800 /health; then
    curl -fsS http://127.0.0.1:8800/health | python3 -c 'import json,sys; c=json.load(sys.stdin)["checks"]; print("banyan checks:", {k: v for k, v in c.items() if k != "roles"}, "roles:", sorted(set(c["roles"].values())))'
  fi
}

case "${1:-}" in
  up) shift; up "$@" ;;
  down) down ;;
  status) status ;;
  logs) tail -f "$LOGS/${2:-banyan}.log" ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
