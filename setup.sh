#!/bin/bash

echo "WebSSH Cluster Setup"
echo "===================="

echo "Installing dependencies..."
pip install -r requirements.txt

if [ -f "setup_serv00_proxy.py" ]; then
    read -p "Run automated Serv00 Proxy Setup (Cloudflare/Ports)? (y/n) [n]: " RUN_PROXY
    if [ "$RUN_PROXY" = "y" ]; then
        python3 setup_serv00_proxy.py
    fi
fi

read -p "Select Mode (master/slave/standalone) [standalone]: " MODE
MODE=${MODE:-standalone}

read -p "Enter Secret Token (for cluster security): " SECRET
echo "Available Ports from devil:"
devil port list
read -p "Enter Port [8888]: " PORT
PORT=${PORT:-8888}

# Try to find the domain mapped to this port
DOMAIN_MAPPING=$(devil www list | grep ":$PORT" | awk '{print $1}')
if [ ! -z "$DOMAIN_MAPPING" ]; then
    echo "This port appears to be mapped to: http://$DOMAIN_MAPPING"
fi

CMD="python run.py --mode=$MODE --port=$PORT --secret=$SECRET"

if [ "$MODE" = "master" ]; then
    read -p "Enter Dashboard Password: " AUTH_PASS
    CMD="$CMD --auth_password=$AUTH_PASS"
    echo "Starting Master on port $PORT..."
elif [ "$MODE" = "slave" ]; then
    read -p "Enter Master URL (e.g., http://master.com): " MASTER_URL
    
    if [ ! -z "$DOMAIN_MAPPING" ]; then
        EXT_URL="http://$DOMAIN_MAPPING"
        echo "Using External URL: $EXT_URL"
    else
        read -p "Enter External URL of this node (e.g., http://s1.serv00.com:$PORT): " EXT_URL
    fi
    
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

read -p "Run with PM2 in background? (y/n) [n]: " USE_PM2
USE_PM2=${USE_PM2:-n}

echo "Generated Command:"
echo "$CMD"

read -p "Do you want to run this now? (y/n): " RUN_NOW
if [ "$RUN_NOW" = "y" ]; then
    if [ "$USE_PM2" = "y" ]; then
        # Extract arguments from CMD (remove 'python run.py ')
        ARGS=${CMD#python run.py }
        NAME="webssh-$MODE-$PORT"
        echo "Starting with PM2 (name: $NAME)..."
        npx pm2 start run.py --name "$NAME" --interpreter python3 -- $ARGS
        npx pm2 save
        echo "Started. Check status with 'npx pm2 status'."
    else
        $CMD
    fi
fi
