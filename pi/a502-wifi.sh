#!/bin/bash
# a502 Wi-Fi helper (runs as root through sudo; installed as /usr/local/sbin/a502-wifi).
# The web server (admin page) calls it. Passwords are stored by NetworkManager (root-only files)
# and are never printed back.
#   a502-wifi list                  saved Wi-Fi networks: uuid<TAB>name<TAB>ssid<TAB>priority<TAB>active<TAB>pw
#                                   pw = stored | open | missing (missing = added via the desktop menu, the
#                                   password sits in a desktop keyring and the Pi cannot use it on its own)
#   a502-wifi scan [rescan]         networks in range (nmcli terse format IN-USE:SSID:SIGNAL:SECURITY)
#   a502-wifi add                   reads 2 lines on stdin: SSID, password (empty = open network)
#   a502-wifi connect UUID          switch to that network in the background (auto roll-back on failure)
#   a502-wifi delete UUID           forget a saved network (not the one in use)
set -u
IF=wlan0
RESULT=/var/lib/a502/wifi.json     # kept across reboots
PREFERRED=100      # priority given to the network the admin chose
OTHER=10           # what the previously chosen one drops to

die() { echo "error: $*" >&2; exit 1; }
uuid_ok() { [[ "${1:-}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || die "bad network id"; }
ssid_of() { nmcli -g 802-11-wireless.ssid con show uuid "$1" 2>/dev/null; }
active_uuid() { nmcli -t -f UUID,TYPE,DEVICE con show --active | awk -F: -v d="$IF" '$2=="802-11-wireless" && $3==d {print $1; exit}'; }
wifi_uuids() { nmcli -t -f UUID,TYPE con show | awk -F: '$2=="802-11-wireless" {print $1}'; }
result() {  # $1 state  $2 message
  local msg=${2//\\/\\\\}; msg=${msg//\"/\\\"}
  printf '{"state":"%s","message":"%s","time":%s}\n' "$1" "$msg" "$(date +%s)" > "$RESULT.tmp"
  chmod 644 "$RESULT.tmp"; mv -f "$RESULT.tmp" "$RESULT"
  logger -t a502-wifi "$1: $2"
}
online() {  # internet (and so the public link) reachable?
  for _ in $(seq 1 8); do                    # up to ~40 s
    curl -s -m 4 -o /dev/null https://controlplane.tailscale.com && return 0
    sleep 1
  done
  return 1
}

cmd=${1:-}; shift || true
case "$cmd" in
list)
  ACT=$(active_uuid)
  for u in $(wifi_uuids); do
    name=$(nmcli -g connection.id con show uuid "$u"); prio=$(nmcli -g connection.autoconnect-priority con show uuid "$u")
    km=$(nmcli -g 802-11-wireless-security.key-mgmt con show uuid "$u" 2>/dev/null)
    if [ -z "$km" ]; then pw=open
    elif [ -n "$(nmcli -s -g 802-11-wireless-security.psk con show uuid "$u" 2>/dev/null)" ]; then pw=stored
    else pw=missing; fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$u" "${name//$'\t'/ }" "$(ssid_of "$u" | tr '\t' ' ')" "${prio:-0}" "$([ "$u" = "$ACT" ] && echo yes || echo no)" "$pw"
  done ;;

scan)
  [ "${1:-}" = rescan ] && { nmcli dev wifi rescan ifname "$IF" 2>/dev/null; sleep 4; }
  nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi list ifname "$IF" ;;

add)
  IFS= read -r SSID || true; IFS= read -r PW || true
  n=$(printf %s "$SSID" | wc -c)
  [ "$n" -ge 1 ] && [ "$n" -le 32 ] || die "network name must be 1-32 characters"
  [[ "$SSID" =~ [[:cntrl:]] ]] && die "network name has invalid characters"
  if [ -n "$PW" ]; then
    [ ${#PW} -ge 8 ] && [ ${#PW} -le 63 ] || die "password must be 8-63 characters (or empty for an open network)"
    [[ "$PW" =~ [[:cntrl:]] ]] && die "password has invalid characters"
  fi
  # WPA3-only router? (scan shows WPA3 but no WPA2) -> use SAE, else WPA2-PSK (also fine for WPA2/WPA3 mixed)
  KM=wpa-psk
  SEC=$(nmcli -t -f SSID,SECURITY dev wifi list ifname "$IF" 2>/dev/null | sed 's/\\:/~/g' | awk -F: -v s="${SSID//:/~}" '$1==s{print $2; exit}')
  [[ "$SEC" == *WPA3* && "$SEC" != *WPA2* ]] && KM=sae
  # which existing entry to keep: the connected one, else one with a usable password, else the first
  U=""; ACT=$(active_uuid); FIRST=""
  for u in $(wifi_uuids); do
    [ "$(ssid_of "$u")" = "$SSID" ] || continue
    [ -z "$FIRST" ] && FIRST=$u
    [ "$u" = "$ACT" ] && { U=$u; break; }
    [ -z "$U" ] && [ -n "$(nmcli -s -g 802-11-wireless-security.psk con show uuid "$u" 2>/dev/null)" ] && U=$u
  done
  [ -z "$U" ] && U=$FIRST
  if [ -n "$U" ]; then           # already saved -> update it (password stored for the system, psk-flags 0)
    nmcli con modify uuid "$U" connection.id "$SSID" 2>/dev/null   # tidy name (e.g. "Iphone 1" -> "Iphone")
    nmcli con modify uuid "$U" ipv4.may-fail no 2>/dev/null       # no IPv4 address = failed -> try the next network
    if [ -n "$PW" ]; then nmcli con modify uuid "$U" wifi-sec.key-mgmt $KM wifi-sec.psk "$PW" wifi-sec.psk-flags 0 connection.autoconnect yes || die "could not update"
    else nmcli con modify uuid "$U" remove 802-11-wireless-security connection.autoconnect yes 2>/dev/null || true; fi
    logger -t a502-wifi "updated saved network $SSID ($KM)"
  else
    if [ -n "$PW" ]; then
      nmcli con add type wifi ifname "$IF" con-name "$SSID" ssid "$SSID" connection.autoconnect yes \
        connection.autoconnect-priority 5 802-11-wireless.powersave 2 ipv4.may-fail no wifi-sec.key-mgmt $KM wifi-sec.psk "$PW" wifi-sec.psk-flags 0 >/dev/null || die "could not save"
    else
      nmcli con add type wifi ifname "$IF" con-name "$SSID" ssid "$SSID" connection.autoconnect yes \
        connection.autoconnect-priority 5 802-11-wireless.powersave 2 ipv4.may-fail no >/dev/null || die "could not save"
    fi
    for u in $(wifi_uuids); do [ "$(ssid_of "$u")" = "$SSID" ] && U=$u; done
    logger -t a502-wifi "saved new network $SSID ($KM)"
  fi
  # remove duplicate entries for the same network name (a stale copy can be picked instead of the good one)
  ACT=$(active_uuid)
  for u in $(wifi_uuids); do
    if [ "$u" != "$U" ] && [ "$u" != "$ACT" ] && [ "$(ssid_of "$u")" = "$SSID" ]; then
      nmcli con delete uuid "$u" >/dev/null 2>&1 && logger -t a502-wifi "removed duplicate entry for $SSID"
    fi
  done
  echo "$U" ;;

connect)
  uuid_ok "${1:-}"; nmcli con show uuid "$1" >/dev/null 2>&1 || die "no such saved network"
  result switching "Connecting to $(ssid_of "$1")…"
  # run in the background: the web page loses its connection while the Wi-Fi changes
  systemd-run --quiet --collect --unit="a502-wifi-switch-$(date +%s)" /usr/local/sbin/a502-wifi _switch "$1" \
    || die "could not start the switch"
  echo started ;;

_switch)
  U=$1; SSID=$(ssid_of "$U"); PREV=$(active_uuid); PSSID=$( [ -n "$PREV" ] && ssid_of "$PREV")
  logger -t a502-wifi "switch: from ${PSSID:-none} to $SSID"
  if [ "$U" = "$PREV" ]; then
    result ok "Already connected to $SSID"
  else
    result switching "Step 1/3: looking for $SSID…"
    FOUND=no
    for _ in 1 2; do                           # scan twice: the first scan sometimes misses networks
      nmcli dev wifi rescan ifname "$IF" 2>/dev/null; sleep 4
      nmcli -t -f SSID dev wifi list ifname "$IF" | sed 's/\\:/:/g' | grep -Fxq -- "$SSID" && { FOUND=yes; break; }
    done
    if [ $FOUND = no ]; then
      result failed "$SSID is not in range of the Pi – stayed on ${PSSID:-the current network}"; exit 0
    fi
    result switching "Step 2/3: joining $SSID (the screen is away for a moment)…"
    ERR=$(nmcli -w 30 con up uuid "$U" 2>&1 >/dev/null)
    if [ $? -eq 0 ]; then
      result switching "Step 3/3: joined $SSID – checking internet…"
      if online; then
        result ok "Connected to $SSID (internet OK)"
      else
        WHY="$SSID has no internet"
      fi
    else
      case "$ERR" in
        *[Ss]ecrets*|*password*|*auth*) WHY="wrong password for $SSID" ;;
        *timeout*|*Timeout*) WHY="$SSID did not answer (timeout)" ;;
        *) WHY="could not join $SSID" ;;
      esac
      logger -t a502-wifi "nmcli: $ERR"
    fi
    if [ -n "${WHY:-}" ]; then
      result rolling_back "$WHY – going back to ${PSSID:-another saved network}…"
      if [ -n "$PREV" ] && nmcli -w 30 con up uuid "$PREV" >/dev/null 2>&1; then
        result failed "Not switched: $WHY. Back on $PSSID."
      else
        nmcli dev connect "$IF" >/dev/null 2>&1   # let NetworkManager pick any saved network
        result failed "Not switched: $WHY. Reconnected to the best saved network."
      fi
      exit 0
    fi
  fi
  # remember the choice: chosen network first after a reboot or a drop-out
  for u in $(wifi_uuids); do
    [ "$(nmcli -g connection.autoconnect-priority con show uuid "$u")" -ge $PREFERRED ] 2>/dev/null &&
      nmcli con modify uuid "$u" connection.autoconnect-priority $OTHER
  done
  nmcli con modify uuid "$U" connection.autoconnect-priority $PREFERRED connection.autoconnect yes ;;

delete)
  uuid_ok "${1:-}"; [ "$1" = "$(active_uuid)" ] && die "that network is in use – connect to another one first"
  S=$(ssid_of "$1"); nmcli con delete uuid "$1" >/dev/null || die "could not forget"
  logger -t a502-wifi "forgot network $S"; echo ok ;;

*) die "usage: a502-wifi list|scan [rescan]|add|connect UUID|delete UUID" ;;
esac
