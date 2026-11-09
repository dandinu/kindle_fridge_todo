#!/bin/sh
# Show fridge loop status and recent log. Retries because the Kindle is only
# reachable while awake (~20 s every 15 min) unless stay-awake is on.
HOST=${HOST:-root@KINDLE_IP}
for i in $(seq 1 ${TRIES:-60}); do
    ssh -o ConnectTimeout=4 "$HOST" "sh /mnt/us/extensions/fridgetodo/bin/fridge.sh status; tail -n ${LINES:-20} /mnt/us/fridgetodo/fridge.log" && exit 0
    sleep 2
done
echo "Kindle not reachable. Use 'Stay awake' in the web UI, or USB: HOST=root@192.168.15.244 $0" >&2
exit 1
