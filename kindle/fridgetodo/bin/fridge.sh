#!/bin/sh
# Fridge to-do display for a jailbroken Kindle.
# Usage: fridge.sh start|stop|stop-loop|run|awake|sleep|status|autostart-on|autostart-off

URL="http://SERVER_IP:8090/kindle.png"
INTERVAL=${INTERVAL:-900}  # seconds between updates while sleeping
AWAKE_INTERVAL=60     # seconds between updates while staying awake
FULL_REFRESH_EVERY=8  # force a full e-ink refresh every N wakeups, even if unchanged
RTC_DEV=rtc0

EXT=/mnt/us/extensions/fridgetodo
STATE=/mnt/us/fridgetodo
FBINK=/mnt/us/koreader/fbink
PIDFILE=$STATE/fridge.pid
STAY_FLAG=$STATE/STAY_AWAKE
LOG=$STATE/fridge.log
UPSTART=/etc/upstart/fridgetodo.conf

mkdir -p "$STATE"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"
    # keep the log small
    if [ "$(wc -l <"$LOG")" -gt 2000 ]; then
        tail -n 1000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    fi
}

loop_pids() { pgrep -f "^/bin/sh $EXT/bin/fridge[.]sh run$"; }

is_running() { [ -n "$(loop_pids)" ]; }

kill_loop() {
    for p in $(loop_pids); do kill "$p"; done
    sleep 2
    for p in $(loop_pids); do kill -9 "$p"; done
    rm -f "$PIDFILE"
}

battery() { lipc-get-prop com.lab126.powerd battLevel 2>/dev/null; }

wifi_up() {
    lipc-get-prop com.lab126.wifid cmState 2>/dev/null | grep -q CONNECTED &&
        ifconfig wlan0 2>/dev/null | grep -q "inet addr:"
}

wifi_on() {
    wifi_up && return 0
    lipc-set-prop com.lab126.cmd wirelessEnable 1
    i=0
    while [ $i -lt 40 ]; do
        wifi_up && return 0
        sleep 1
        i=$((i + 1))
    done
    log "wifi did not connect"
    return 1
}

wifi_off() { lipc-set-prop com.lab126.cmd wirelessEnable 0; }

# Dropbear forks one child per connection; more than one process means someone is logged in.
ssh_active() { [ "$(pidof dropbear | wc -w)" -gt 1 ]; }

draw_footer() {
    "$FBINK" -q -m -y -2 -S 2 "Checked $(date '+%H:%M')  |  battery $(battery)%  $1" >/dev/null 2>&1
}

fetch_and_draw() {
    # $1 = 1 to force a full refresh
    etag=$(cat "$STATE/etag" 2>/dev/null)
    code=$(curl -s -m 25 -o "$STATE/new.png" -D "$STATE/headers" \
        -H "If-None-Match: $etag" -w '%{http_code}' "$URL?bat=$(battery)")
    awake_secs=$(grep -i '^X-Stay-Awake:' "$STATE/headers" 2>/dev/null | tr -dc '0-9')
    if [ -n "$awake_secs" ]; then
        echo $(($(date +%s) + awake_secs)) >"$STATE/awake_until"
    else
        rm -f "$STATE/awake_until"
    fi

    case "$code" in
        200)
            mv "$STATE/new.png" "$STATE/todo.png"
            grep -i '^ETag:' "$STATE/headers" | sed 's/^[Ee][Tt][Aa][Gg]: *//' | tr -d '\r' >"$STATE/etag"
            eips -f -g "$STATE/todo.png" >/dev/null 2>&1
            draw_footer
            log "updated (200)"
            ;;
        304)
            if [ "$1" = 1 ] && [ -f "$STATE/todo.png" ]; then
                eips -f -g "$STATE/todo.png" >/dev/null 2>&1
            fi
            draw_footer
            log "unchanged (304)"
            ;;
        *)
            [ "$1" = 1 ] && [ -f "$STATE/todo.png" ] && eips -f -g "$STATE/todo.png" >/dev/null 2>&1
            draw_footer "|  OFFLINE"
            log "fetch failed (code $code)"
            ;;
    esac
}

should_stay_awake() {
    [ -f "$STAY_FLAG" ] && return 0
    until=$(cat "$STATE/awake_until" 2>/dev/null)
    [ -n "$until" ] && [ "$(date +%s)" -lt "$until" ] && return 0
    ssh_active && return 0
    return 1
}

rtc_suspend() {
    # Suspend to RAM; the RTC alarm wakes us after $1 seconds.
    echo 0 >/sys/class/rtc/$RTC_DEV/wakealarm
    echo "+$1" >/sys/class/rtc/$RTC_DEV/wakealarm
    echo mem >/sys/power/state
}

ui_stop() {
    lipc-set-prop com.lab126.powerd preventScreenSaver 1
    stop lab126_gui >/dev/null 2>&1
    stop framework >/dev/null 2>&1
    sleep 2
    lipc-set-prop com.lab126.powerd flIntensity 0 2>/dev/null
}

ui_start() {
    lipc-set-prop com.lab126.powerd preventScreenSaver 0
    start lab126_gui >/dev/null 2>&1
    start framework >/dev/null 2>&1
}

run_loop() {
    echo $$ >"$PIDFILE"
    trap '[ "$(cat "$PIDFILE" 2>/dev/null)" = "$$" ] && rm -f "$PIDFILE"; exit 0' TERM INT
    log "started"
    ui_stop
    n=0
    while true; do
        full=0
        [ $((n % FULL_REFRESH_EVERY)) -eq 0 ] && full=1
        if wifi_on; then
            fetch_and_draw $full
        else
            [ "$full" = 1 ] && [ -f "$STATE/todo.png" ] && eips -f -g "$STATE/todo.png" >/dev/null 2>&1
            draw_footer "|  NO WIFI"
        fi
        n=$((n + 1))

        if should_stay_awake; then
            sleep $AWAKE_INTERVAL &
            wait $!
        else
            wifi_off
            sleep 1
            log "suspending for ${INTERVAL}s"
            rtc_suspend $INTERVAL
            log "resumed"
            # After resume give the system a moment before touching Wi-Fi.
            sleep 3
        fi
    done
}

case "$1" in
    start)
        if is_running; then echo "already running"; exit 0; fi
        nohup /bin/sh "$EXT/bin/fridge.sh" run >/dev/null 2>&1 &
        echo "started"
        ;;
    run)
        run_loop
        ;;
    stop-loop)
        # Kill the loop only (used by deploy); leaves the screen and UI as they are.
        kill_loop
        echo "loop stopped"
        ;;
    stop)
        kill_loop
        wifi_on >/dev/null
        log "stopped"
        ui_start
        echo "stopped"
        ;;
    awake)
        touch "$STAY_FLAG"
        wifi_on >/dev/null
        "$FBINK" -q -m -y -2 -S 2 "Stay-awake ON (Wi-Fi and SSH stay up)" >/dev/null 2>&1
        echo "stay-awake on"
        ;;
    sleep)
        rm -f "$STAY_FLAG"
        echo "stay-awake off"
        ;;
    status)
        is_running && echo "running (pid $(loop_pids | tr '\n' ' '))" || echo "not running"
        [ -f "$STAY_FLAG" ] && echo "stay-awake flag: on" || echo "stay-awake flag: off"
        [ -f "$STATE/awake_until" ] && echo "server stay-awake until: $(date -d @"$(cat "$STATE/awake_until")" 2>/dev/null || cat "$STATE/awake_until")"
        [ -f "$UPSTART" ] && echo "autostart: on" || echo "autostart: off"
        tail -n 5 "$LOG" 2>/dev/null
        ;;
    autostart-on)
        mntroot rw
        cat >"$UPSTART" <<EOF
start on started wifid and started lab126_gui
script
    # Grace period so you can SSH in and run "fridge.sh autostart-off" if something goes wrong.
    sleep 90
    [ -f $STATE/DISABLE_AUTOSTART ] && exit 0
    exec /bin/sh $EXT/bin/fridge.sh run
end script
EOF
        mntroot ro
        echo "autostart on"
        ;;
    autostart-off)
        mntroot rw
        rm -f "$UPSTART"
        mntroot ro
        echo "autostart off"
        ;;
    *)
        echo "usage: $0 start|stop|stop-loop|run|awake|sleep|status|autostart-on|autostart-off"
        exit 1
        ;;
esac
