#!/bin/sh
# Exit if another instance is already running
for p in $(pgrep -f "^/bin/sh .*wifi_ssh_keepalive[.]sh$"); do
    [ "$p" != "$$" ] && exit 0
done
while true; do
    if ifconfig wlan0 2>/dev/null | grep -q "inet addr:"; then
        if ! iptables -C INPUT -i wlan0 -p tcp --dport 22 -j ACCEPT 2>/dev/null; then
            iptables -I INPUT 1 -i wlan0 -p tcp --dport 22 -j ACCEPT
        fi
        iwconfig wlan0 2>/dev/null | grep -q "Power Management:off" || iwconfig wlan0 power off 2>/dev/null
    fi
    sleep 15
done
