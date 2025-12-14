#!/bin/bash

echo "WebSSH Cluster Setup"
echo "===================="

read -p "Select Mode (master/slave/standalone) [standalone]: " MODE
MODE=${MODE:-standalone}

read -p "Enter Secret Token (for cluster security): " SECRET
read -p "Enter Port [8888]: " PORT
PORT=${PORT:-8888}

CMD="python run.py --mode=$MODE --port=$PORT --secret=$SECRET"

if [ "$MODE" = "master" ]; then
    read -p "Enter Dashboard Password: " AUTH_PASS
    CMD="$CMD --auth_password=$AUTH_PASS"
    echo "Starting Master on port $PORT..."
elif [ "$MODE" = "slave" ]; then
    read -p "Enter Master URL (e.g., http://master.com): " MASTER_URL
    read -p "Enter External URL of this node (e.g., http://s1.serv00.com:$PORT): " EXT_URL
    
    echo "--- SSH Target Configuration (Optional) ---"
    read -p "Target SSH Host [localhost]: " SSH_HOST
    SSH_HOST=${SSH_HOST:-localhost}
    read -p "Target SSH Port [22]: " SSH_PORT
    SSH_PORT=${SSH_PORT:-22}
    read -p "Target SSH User [root]: " SSH_USER
    SSH_USER=${SSH_USER:-root}
    read -p "Target SSH Password: " SSH_PASS
    
    CMD="$CMD --master_url=$MASTER_URL --external_url=$EXT_URL --ssh_host=$SSH_HOST --ssh_port=$SSH_PORT --ssh_user=$SSH_USER"
    if [ ! -z "$SSH_PASS" ]; then
        CMD="$CMD --ssh_password=$SSH_PASS"
    fi
    
    echo "Starting Slave on port $PORT..."
fi

echo "Generated Command:"
echo "$CMD"

read -p "Do you want to run this now? (y/n): " RUN_NOW
if [ "$RUN_NOW" = "y" ]; then
    $CMD
fi
