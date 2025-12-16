#!/usr/bin/env python3
"""
Dashboard Password Rotation Script for WebSSH Cluster

This script updates the dashboard login password on the Master node(s).
It connects via SSH to the Master server(s), updates the PM2 process with the new password, and restarts it.

Usage:
    python scripts/update_password.py --new-password YOUR_NEW_PASSWORD

Environment Variables:
    SERVERS_JSON: JSON array of server configurations
    MASTER_PASSWORD: New password (can be used instead of --new-password)
"""

import argparse
import json
import os
import sys
import paramiko
import time


def parse_args():
    parser = argparse.ArgumentParser(description="Rotate dashboard login password")
    parser.add_argument("--new-password", help="New dashboard password")
    parser.add_argument("--servers-json", help="Path to servers JSON file or env var SERVERS_JSON")
    return parser.parse_args()


def get_servers_config(servers_json_arg):
    """
    Load server configuration from argument, environment variable, or file.
    """
    servers_json = servers_json_arg or os.environ.get("SERVERS_JSON")
    servers_file = os.environ.get("SERVERS_FILE", "servers.json")
    
    # Priority: CLI arg > env var > file
    if servers_json:
        try:
            servers = json.loads(servers_json)
            return servers
        except json.JSONDecodeError:
            # Maybe it's a file path
            if os.path.isfile(servers_json):
                with open(servers_json, 'r') as f:
                    return json.load(f)
            else:
                print(f"Error: Could not parse SERVERS_JSON as JSON: {servers_json}")
                sys.exit(1)
    elif os.path.isfile(servers_file):
        print(f"Loading servers from file: {servers_file}")
        with open(servers_file, 'r') as f:
            return json.load(f)
    else:
        print("Error: SERVERS_JSON not provided via --servers-json, environment variable, or servers.json file")
        sys.exit(1)


def execute_ssh_command(client, command, timeout=10):
    """Execute a command via SSH and return stdout, stderr."""
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        stdout_text = stdout.read().decode('utf-8', errors='replace')
        stderr_text = stderr.read().decode('utf-8', errors='replace')
        return exit_code, stdout_text, stderr_text
    except Exception as e:
        return -1, "", str(e)


def update_master_password(server, new_password):
    """Connect to a server and update the WebSSH PM2 process with new password."""
    host = server.get("host")
    port = int(server.get("port", 22))
    username = server.get("username")
    password = server.get("password")
    work_path = server.get("path", "~")
    
    print(f"\n{'='*60}")
    print(f"Updating Master: {username}@{host}:{port}")
    print(f"Working directory: {work_path}")
    print(f"{'='*60}")
    
    try:
        # Connect via SSH
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=host, port=port, username=username, password=password, timeout=15)
        
        # Change to working directory
        cd_prefix = f"cd {work_path} && " if work_path and work_path != "~" else ""
        
        # Step 1: Get PM2 process list
        print("  → Fetching PM2 processes...")
        exit_code, stdout, stderr = execute_ssh_command(client, f"{cd_prefix}npx pm2 jlist")
        
        if exit_code != 0:
            print(f"  ✗ Failed to get PM2 list: {stderr}")
            client.close()
            return False
        
        try:
            pm2_list = json.loads(stdout)
        except json.JSONDecodeError:
            print(f"  ✗ Failed to parse PM2 output")
            client.close()
            return False
        
        # Step 2: Find webssh process
        webssh_process = None
        for proc in pm2_list:
            proc_name = proc.get('name', '')
            if 'webssh' in proc_name.lower():
                webssh_process = proc
                break
        
        if not webssh_process:
            print("  ⚠ No WebSSH process found (skipping)")
            client.close()
            return False 
        
        process_name = webssh_process.get('name')
        print(f"  → Found process: {process_name}")
        
        # Step 3: Extract current arguments
        pm2_env = webssh_process.get('pm2_env', {})
        args = pm2_env.get('args', [])
        
        if isinstance(args, str):
            args = args.split()
        
        print(f"  → Current args: {' '.join(args)}")
        
        # Step 4: Replace or Add --auth-password argument
        new_args = []
        password_found = False
        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith('--auth-password='):
                new_args.append(f'--auth-password={new_password}')
                password_found = True
                i += 1
            elif arg == '--auth-password':
                new_args.append('--auth-password')
                new_args.append(new_password)
                password_found = True
                i += 2  # Skip the next argument (old password value)
            else:
                new_args.append(arg)
                i += 1
        
        if not password_found:
            print("  → Adding new --auth-password argument")
            new_args.append(f'--auth-password={new_password}')
        else:
            print("  → Updating existing --auth-password argument")
            
        new_args_str = ' '.join(new_args)
        
        # Step 5: Restart with PM2
        interpreter = pm2_env.get('exec_interpreter', 'python3')
        script = pm2_env.get('pm_exec_path', 'run.py')
        
        print(f"  → Restarting process...")
        
        # Delete old process
        exit_code, stdout, stderr = execute_ssh_command(client, f"{cd_prefix}npx pm2 delete {process_name}")
        if exit_code != 0:
            print(f"  ⚠ Warning during delete: {stderr}")
        
        # Start with new args
        start_cmd = f'{cd_prefix}npx pm2 start {script} --name "{process_name}" --interpreter {interpreter} -- {new_args_str}'
        exit_code, stdout, stderr = execute_ssh_command(client, start_cmd, timeout=20)
        
        if exit_code != 0:
            print(f"  ✗ Failed to start: {stderr}")
            client.close()
            return False
        
        # Save PM2 state
        execute_ssh_command(client, f"{cd_prefix}npx pm2 save")
        
        print(f"  ✓ Successfully updated master password")
        client.close()
        return True
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False


def main():
    args = parse_args()
    new_password = args.new_password or os.environ.get("MASTER_PASSWORD")
    
    if not new_password:
        print("Error: No new password provided via --new-password or MASTER_PASSWORD env var")
        sys.exit(1)
        
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║         WebSSH Master Password Update Tool              ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    
    # Load server configuration
    servers = get_servers_config(args.servers_json)
    
    # Find Master servers
    master_servers = [s for s in servers if s.get('role') == 'master']
    
    if not master_servers:
        print("\nError: No server with 'role': 'master' found in configuration.")
        sys.exit(1)
        
    print(f"\nFound {len(master_servers)} Master server(s)")
    
    success_count = 0
    failed_count = 0
    
    for server in master_servers:
        if update_master_password(server, new_password):
            success_count += 1
        else:
            failed_count += 1
            
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total Masters: {len(master_servers)}")
    print(f"Updated: {success_count}")
    print(f"Failed: {failed_count}")
    
    if failed_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
