#!/usr/bin/env bash
# Ship Banyan (and the llm-steward / llm-gateway it runs with) to the Oracle VM (deploy/oracle/README.md).
#
#   deploy/oracle/ship.sh [banyan] [steward] [gateway]   default: all three
#
# Only committed code goes over (`git archive HEAD`; a dirty tree is refused). On the VM: `uv sync --frozen`,
# the systemd user units and the Caddy config are installed, the services restart, and a smoke check asks
# Banyan's /health (identity and every check) through Caddy. Values live in the VM's ~/.config files, never here.
set -euo pipefail

HOST="${BANYAN_VM:-banyan-vm}"
BANYAN_REPO="$(cd "$(dirname "$0")/../.." && pwd)"
STEWARD_REPO="${BANYAN_STEWARD_REPO:-$HOME/Code/AI/lite-ai/llm-steward}"
GATEWAY_REPO="${BANYAN_GATEWAY_REPO:-$HOME/Code/AI/lite-ai/llm-gateway}"
PARTS=("$@")
((${#PARTS[@]})) || PARTS=(banyan steward gateway)

clean() {  # repo
  if [[ -n "$(git -C "$1" status --porcelain --untracked-files=no)" ]]; then
    echo "$1 has uncommitted changes: commit them first (only HEAD is shipped)" >&2
    exit 1
  fi
}

send() {  # repo remote-dir
  clean "$1"
  echo "→ $(basename "$1") $(git -C "$1" rev-parse --short HEAD) to $HOST:$2"
  # Into a fresh copy, then synced over the live tree: files gone from HEAD go, the venvs stay.
  git -C "$1" archive --format=tar HEAD | ssh "$HOST" \
    "rm -rf ~/$2.new && mkdir -p ~/$2.new ~/$2 && tar -x -C ~/$2.new && rsync -a --delete --exclude .venv/ ~/$2.new/ ~/$2/ && rm -rf ~/$2.new"
}

for part in "${PARTS[@]}"; do
  case "$part" in
    banyan) send "$BANYAN_REPO" banyan ;;
    steward) send "$STEWARD_REPO" llm-steward ;;
    gateway) send "$GATEWAY_REPO" llm-gateway ;;
    *) echo "unknown part: $part" >&2; exit 2 ;;
  esac
done

ssh "$HOST" 'bash -s' -- "${PARTS[@]}" <<'REMOTE'
set -euo pipefail
UV="$HOME/.local/bin/uv"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
for part in "$@"; do
  case "$part" in
    gateway)
      (cd ~/llm-gateway && "$UV" sync --frozen -q)
      cp ~/llm-gateway/deploy/llmgw.service "$units/" ;;
    steward)
      (cd ~/llm-steward/backend && "$UV" sync --frozen -q --extra literag --extra litemem)
      cp ~/banyan/deploy/oracle/banyan-steward.service "$units/" ;;
    banyan)
      (cd ~/banyan/backend && "$UV" sync --frozen -q --no-dev)
      cp ~/banyan/deploy/oracle/banyan-api.service "$units/"
      sudo install -m 644 ~/banyan/deploy/oracle/Caddyfile /etc/caddy/Caddyfile
      sudo systemctl reload caddy ;;
  esac
done
systemctl --user daemon-reload
for part in "$@"; do
  case "$part" in
    gateway) systemctl --user enable -q llmgw && systemctl --user restart llmgw ;;
    steward) systemctl --user enable -q banyan-steward && systemctl --user restart banyan-steward ;;
    banyan) systemctl --user enable -q banyan-api && systemctl --user restart banyan-api ;;
  esac
done
REMOTE

[[ " ${PARTS[*]} " == *" banyan "* ]] || exit 0
# Smoke: Banyan's /health through Caddy, every check ok.
api="$(ssh "$HOST" 'grep -E "^BANYAN_API_HOST=" /etc/default/caddy | cut -d= -f2')"
for _ in $(seq 1 60); do
  if body="$(curl -fsS --max-time 10 "https://$api/health" 2>/dev/null)"; then
    # Banyan's shape (not some other server answering 200): checks for db, steward, search and roles.
    echo "$body" | python3 -c 'import json,sys; h=json.load(sys.stdin); c=h["checks"]; assert {"db","steward","search","roles"} <= set(c), h; print("health:", h["status"], {k: v for k, v in c.items() if k != "roles"}); sys.exit(0 if h["status"] == "ok" else 1)'
    exit 0
  fi
  sleep 3
done
echo "https://$api/health did not answer: ssh $HOST journalctl --user -u banyan-api -n 50" >&2
exit 1
