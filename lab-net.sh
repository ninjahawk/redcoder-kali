#!/usr/bin/env bash
#
# lab-net.sh — build an ISOLATED OFFLINE LAB network for redcoder's `--lab` mode.
#
# Creates a network namespace ("rclab") wired to a Linux bridge that has NO physical
# uplink. Tools run inside rclab (nmap, sqlmap, ...) can reach fake/lab targets you
# attach to the bridge, but there is NO wire to the internet — the bridge is never
# connected to a real NIC, so packets have nowhere to go but other lab hosts.
#
# THE SAFETY INVARIANT: the bridge (rclab-br) is never enslaved to eth0/wlan0/any
# physical interface. That alone makes the internet physically unreachable from the
# lab, regardless of routes. `verify` PROVES it by trying to reach public IPs and
# requiring failure. redcoder ALSO re-verifies before it will enter lab mode.
#
# This is runtime kernel state: it does NOT survive a reboot. Re-run `up` each boot
# (or wire it into a systemd unit). It touches only virtual devices — never a disk,
# never a physical NIC.
#
#   sudo ./lab-net.sh up        build + verify the lab network
#   sudo ./lab-net.sh verify    prove the internet is unreachable from the lab
#   sudo ./lab-net.sh status    show what exists
#   sudo ./lab-net.sh down      tear it all down
#
# Attach a fake target: give any container/VM/veth a 10.66.0.x address on rclab-br.
# See "attach a target" at the bottom for a one-liner that spawns a throwaway target.

set -euo pipefail

NS="rclab"                    # must match LAB_NETNS in redcoder.py
BR="rclab-br"
SUBNET="10.66.0.0/24"         # must match LAB_SUBNET in redcoder.py
BR_IP="10.66.0.1/24"
NS_IP="10.66.0.10/24"
VETH_NS="veth-rclab"          # end inside the namespace
VETH_BR="veth-rclab-br"       # end on the bridge

say()  { printf '\n\033[1;31m==>\033[0m \033[1m%s\033[0m\n' "$1"; }
ok()   { printf '    \033[32m%s\033[0m\n' "$1"; }
warn() { printf '    \033[33m! %s\033[0m\n' "$1"; }
die()  { printf '    \033[31m%s\033[0m\n' "$1" >&2; exit 1; }

# Need root to create namespaces / bridges / veth.
if [ "$(id -u)" != "0" ]; then exec sudo -- "$0" "$@"; fi

have() { command -v "$1" >/dev/null 2>&1; }
have ip || die "iproute2 (ip) is required: sudo apt install -y iproute2"

up() {
  say "Building the offline lab network ($NS on $BR, $SUBNET)"

  # Namespace
  if ip netns list | awk '{print $1}' | grep -qx "$NS"; then
    ok "namespace '$NS' already exists"
  else
    ip netns add "$NS"; ok "created namespace '$NS'"
  fi

  # Bridge — with NO physical uplink. This is the whole safety story.
  if ip link show "$BR" >/dev/null 2>&1; then
    ok "bridge '$BR' already exists"
  else
    ip link add name "$BR" type bridge
    ip addr add "$BR_IP" dev "$BR" 2>/dev/null || true
    ip link set "$BR" up
    ok "created bridge '$BR' ($BR_IP) — no physical uplink, by design"
  fi

  # veth pair linking the namespace to the bridge
  if ip link show "$VETH_BR" >/dev/null 2>&1; then
    ok "veth already present"
  else
    ip link add "$VETH_BR" type veth peer name "$VETH_NS"
    ip link set "$VETH_NS" netns "$NS"
    ip link set "$VETH_BR" master "$BR"
    ip link set "$VETH_BR" up
    ip netns exec "$NS" ip addr add "$NS_IP" dev "$VETH_NS"
    ip netns exec "$NS" ip link set "$VETH_NS" up
    ip netns exec "$NS" ip link set lo up
    ok "wired $NS ($NS_IP) to $BR via veth"
  fi

  # DELIBERATELY no default route in the namespace and no NAT/ip_forward changes.
  # There is nothing to route to: the bridge has no uplink. The lab can talk to
  # 10.66.0.0/24 and nothing else.
  ok "no default route, no NAT — lab reaches $SUBNET only"

  verify
}

down() {
  say "Tearing down the lab network"
  if ip netns list | awk '{print $1}' | grep -qx "$NS"; then
    ip netns del "$NS" && ok "deleted namespace '$NS' (its veth end went with it)"
  else
    ok "namespace '$NS' not present"
  fi
  ip link del "$VETH_BR" 2>/dev/null && ok "removed leftover veth" || true
  if ip link show "$BR" >/dev/null 2>&1; then
    ip link del "$BR" && ok "deleted bridge '$BR'"
  else
    ok "bridge '$BR' not present"
  fi
}

status() {
  say "Lab network status"
  if ip netns list | awk '{print $1}' | grep -qx "$NS"; then
    ok "namespace: $NS"
    ip netns exec "$NS" ip -brief addr show 2>/dev/null | sed 's/^/      /'
  else
    warn "namespace '$NS' does not exist — run: sudo $0 up"
  fi
  if ip link show "$BR" >/dev/null 2>&1; then
    ok "bridge: $BR"
    # List anything enslaved to the bridge (targets you attached).
    ip link show master "$BR" 2>/dev/null | awk -F': ' '/@|:/{print "      "$2}' | sed 's/@.*//' || true
    # Safety check: a physical NIC must NEVER be on this bridge.
    if ip link show master "$BR" 2>/dev/null | grep -Eq 'eth[0-9]|wlan[0-9]|en[a-z0-9]+|wl[a-z0-9]+'; then
      die "DANGER: a physical interface is enslaved to $BR — the lab is NOT airgapped. Fix immediately."
    fi
  else
    warn "bridge '$BR' does not exist"
  fi
}

verify() {
  say "Verifying the airgap (must be UNREACHABLE)"
  ip netns list | awk '{print $1}' | grep -qx "$NS" || die "namespace '$NS' missing — run: sudo $0 up"

  # Physical-NIC-on-bridge guard (defence in depth).
  if ip link show "$BR" >/dev/null 2>&1 && \
     ip link show master "$BR" 2>/dev/null | grep -Eq 'eth[0-9]|wlan[0-9]|en[a-z0-9]+|wl[a-z0-9]+'; then
    die "FAIL: a physical interface is on $BR — internet may be reachable. NOT SAFE."
  fi

  # Active egress probe from inside the namespace: try to open a TCP connection to
  # several public resolvers. ANY success means the airgap is broken.
  local reached=""
  for ip_addr in 1.1.1.1 8.8.8.8 9.9.9.9; do
    if ip netns exec "$NS" timeout 2 bash -c "exec 3<>/dev/tcp/${ip_addr}/443" 2>/dev/null; then
      reached="$ip_addr"; break
    fi
  done
  if [ -n "$reached" ]; then
    die "FAIL: reached $reached from the lab — the internet is REACHABLE. NOT SAFE. Run 'down' and investigate."
  fi
  ok "internet is UNREACHABLE from '$NS' — airgap confirmed"
  ok "redcoder --lab is safe to use"
}

case "${1:-}" in
  up)      up ;;
  down)    down ;;
  status)  status ;;
  verify)  verify ;;
  *)
    cat <<EOF
lab-net.sh — offline lab network for redcoder --lab

  sudo $0 up        build + verify the isolated lab net ($SUBNET)
  sudo $0 verify    prove the internet is unreachable from the lab
  sudo $0 status    show namespace / bridge / attached targets
  sudo $0 down      tear it all down

Attach a throwaway fake target (second namespace on the same bridge):

  sudo ip netns add rclab-t1
  sudo ip link add veth-t1 type veth peer name veth-t1-br
  sudo ip link set veth-t1 netns rclab-t1
  sudo ip link set veth-t1-br master $BR
  sudo ip link set veth-t1-br up
  sudo ip netns exec rclab-t1 ip addr add 10.66.0.20/24 dev veth-t1
  sudo ip netns exec rclab-t1 ip link set veth-t1 up
  sudo ip netns exec rclab-t1 ip link set lo up
  # start a service in it, e.g.:  sudo ip netns exec rclab-t1 python3 -m http.server 80
  # then from redcoder --lab:     nmap 10.66.0.0/24    (finds 10.66.0.20, never the internet)
EOF
    ;;
esac
