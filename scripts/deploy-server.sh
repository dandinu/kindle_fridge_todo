#!/bin/sh
# Deploy the server to the Docker host and rebuild the container.
set -e
HOST=${HOST:-root@SERVER_IP}
DIR=/opt/kindle-todo
cd "$(dirname "$0")/../server"

ssh "$HOST" "mkdir -p $DIR"
scp -rq Dockerfile docker-compose.yml app.py static "$HOST:$DIR/"
ssh "$HOST" "cd $DIR && [ -f .env ] || echo \"API_TOKEN=\$(head -c 18 /dev/urandom | base64 | tr -d /+=)\" > .env; docker compose up -d --build"
echo "Deployed. UI: http://SERVER_IP:8090"
