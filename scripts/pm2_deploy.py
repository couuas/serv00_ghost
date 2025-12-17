import json
import os
import paramiko
import time
import sys

def main():
    servers_json = os.environ.get("SERVERS_JSON")
    if not servers_json:
        print("::error::SERVERS_JSON secret is missing or empty.")
        sys.exit(1)

    try:
        servers = json.loads(servers_json)
    except json.JSONDecodeError as e:
        print(f"::error::Failed to parse SERVERS_JSON: {e}")
        sys.exit(1)

    if not isinstance(servers, list):
           print("::error::SERVERS_JSON must be a list of objects.")
           sys.exit(1)

    success_count = 0
    for server in servers:
        host = server.get("host")
        port = int(server.get("port", 22))
        username = server.get("username")
        password = server.get("password")
        path = server.get("path", "~/serv00_ghost")
        
        print(f"--- Connecting to {username}@{host}:{port} ---")
        
        try:
            # Initialize SSH client
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=host, port=port, username=username, password=password, timeout=30)
            
            # Command to execute
            # 1. cd to directory
            # 2. git pull
            # 3. Read .pm2_app_name and restart if exists
            cmd = (
                f"cd {path} && git pull && "
                "echo 'Restarting ALL PM2 services...' && "
                "npx -y pm2 restart all"
            )
            print(f"Executing deployment and restart sequence...")
            
            stdin, stdout, stderr = client.exec_command(cmd)
            
            # Wait for command to complete and get exit status
            exit_status = stdout.channel.recv_exit_status()
            
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()
            
            if out: print(f"[stdout]\n{out}")
            if err: print(f"[stderr]\n{err}")
            
            if exit_status == 0:
                print(f"✅ Successfully deployed to {host}")
                success_count += 1
            else:
                print(f"❌ Failed to deploy to {host}. Exit code: {exit_status}")
            
            client.close()
        except Exception as e:
            print(f"❌ Connection/Execution error for {host}: {e}")

    print(f"\nDeployment summary: {success_count}/{len(servers)} successful.")
    if success_count != len(servers):
        sys.exit(1)

if __name__ == "__main__":
    main()
