#!/bin/sh
# Deploy the KUAL extension to the Kindle. Stops the loop first (a running
# shell script must not be overwritten), then restarts it unless --no-start.
set -e
HOST=${HOST:-root@KINDLE_IP}
EXT=/mnt/us/extensions/fridgetodo
cd "$(dirname "$0")/../kindle"

ssh "$HOST" "[ -x $EXT/bin/fridge.sh ] && sh $EXT/bin/fridge.sh stop-loop || true"
scp -rq fridgetodo "$HOST:/mnt/us/extensions/"
ssh "$HOST" "chmod +x $EXT/bin/fridge.sh"
[ "$1" = "--no-start" ] || ssh "$HOST" "sh $EXT/bin/fridge.sh start"
