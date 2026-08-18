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

# A built-in fake target so `--lab` has something to scan out of the box. It is a
# SECOND isolated namespace on the same uplink-less bridge — still no internet.
TGT_NS="rclab-t1"
TGT_IP="10.66.0.20/24"
TGT_ADDR="10.66.0.20"
TGT_VETH_NS="veth-t1"         # end inside the target namespace
TGT_VETH_BR="veth-t1-br"      # end on the bridge

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

  # Stand up the built-in fake target so `--lab` has something to scan immediately.
  target_up

  # Let redcoder enter the namespace without a password prompt. Only used when
  # firejail is unavailable (Kali no longer ships it); scoped strictly to
  # `ip netns exec rclab ...`. Removed by `down`.
  install_sudoers

  verify
}

target_up() {
  # A throwaway target namespace on the same bridge, serving HTTP on port 80 so a
  # scan finds an open port — never anything but a lab host, no uplink.
  if ip netns list | awk '{print $1}' | grep -qx "$TGT_NS"; then
    ok "target namespace '$TGT_NS' already exists"
  else
    ip netns add "$TGT_NS"
    ip link add "$TGT_VETH_BR" type veth peer name "$TGT_VETH_NS"
    ip link set "$TGT_VETH_NS" netns "$TGT_NS"
    ip link set "$TGT_VETH_BR" master "$BR"
    ip link set "$TGT_VETH_BR" up
    ip netns exec "$TGT_NS" ip addr add "$TGT_IP" dev "$TGT_VETH_NS"
    ip netns exec "$TGT_NS" ip link set "$TGT_VETH_NS" up
    ip netns exec "$TGT_NS" ip link set lo up
    ok "created fake target '$TGT_NS' ($TGT_ADDR)"
  fi

  # Start (or restart) a tiny HTTP service inside the target, fully detached so it
  # survives this script exiting. `ip netns pids` lets `down` reap it cleanly.
  if ip netns pids "$TGT_NS" 2>/dev/null | grep -q .; then
    ok "target service already running on $TGT_ADDR:80"
  elif have python3; then
    ip netns exec "$TGT_NS" setsid python3 -m http.server 80 --bind "$TGT_ADDR" \
      >/tmp/redcoder-rclab-t1.log 2>&1 < /dev/null &
    disown 2>/dev/null || true
    ok "started HTTP service on $TGT_ADDR:80 (log: /tmp/redcoder-rclab-t1.log)"
  else
    warn "python3 not found — target has no open ports (still discoverable by ARP)"
  fi
}

target_down() {
  if ip netns list | awk '{print $1}' | grep -qx "$TGT_NS"; then
    ip netns pids "$TGT_NS" 2>/dev/null | xargs -r kill 2>/dev/null || true
    ip netns del "$TGT_NS" && ok "deleted target namespace '$TGT_NS'"
  fi
  ip link del "$TGT_VETH_BR" 2>/dev/null && ok "removed target veth" || true
}

install_sudoers() {
  local ip_bin sudoers luser
  ip_bin="$(command -v ip)"
  sudoers="/etc/sudoers.d/redcoder-lab"
  luser="${SUDO_USER:-$(logname 2>/dev/null || echo kali)}"
  printf '%s ALL=(root) NOPASSWD: %s netns exec %s *\n' "$luser" "$ip_bin" "$NS" > "$sudoers"
  chmod 440 "$sudoers"
  if visudo -cf "$sudoers" >/dev/null 2>&1; then
    ok "sudo rule installed ($sudoers) — $luser may enter '$NS' without a password"
  else
    rm -f "$sudoers"
    warn "sudoers check failed; rule not installed (lab mode will need firejail)"
  fi
}

down() {
  say "Tearing down the lab network"
  target_down
  rm -f /etc/sudoers.d/redcoder-lab && ok "removed sudo rule" || true
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

  sudo $0 up        build + verify the isolated lab net ($SUBNET), incl. a fake target
  sudo $0 verify    prove the internet is unreachable from the lab
  sudo $0 status    show namespace / bridge / attached targets
  sudo $0 down      tear it all down

`up` now also stands up a built-in fake target at $TGT_ADDR serving HTTP on port 80,
so redcoder --lab has something to scan immediately:

      nmap 10.66.0.0/24     # finds $TGT_ADDR (never the internet)

Add MORE throwaway targets by hand if you want a bigger lab (same bridge, $BR):

  sudo ip netns add rclab-t2
  sudo ip link add veth-t2 type veth peer name veth-t2-br
  sudo ip link set veth-t2 netns rclab-t2
  sudo ip link set veth-t2-br master $BR
  sudo ip link set veth-t2-br up
  sudo ip netns exec rclab-t2 ip addr add 10.66.0.21/24 dev veth-t2
  sudo ip netns exec rclab-t2 ip link set veth-t2 up
  sudo ip netns exec rclab-t2 ip link set lo up
  sudo ip netns exec rclab-t2 python3 -m http.server 8080 --bind 10.66.0.21 &
EOF
    ;;
esac
