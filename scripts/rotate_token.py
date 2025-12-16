#!/usr/bin/env python3
"""
Token Rotation Script for WebSSH Cluster

This script rotates the cluster secret token across all nodes (Master and Slaves).
It connects via SSH to each server, updates the PM2 process with the new token, and restarts it.

Usage:
    python scripts/rotate_token.py --new-token YOUR_NEW_TOKEN

Environment Variables:
    SERVERS_JSON: JSON array of server configurations (same format as health check)
"""

import argparse
import json
import os
import sys
import paramiko
import time


def parse_args():
    parser = argparse.ArgumentParser(description="Rotate cluster secret token")
    parser.add_argument("--new-token", required=True, help="New secret token to use")
    parser.add_argument("--servers-json", help="Path to servers JSON file or env var SERVERS_JSON")
    return parser.parse_args()


def get_servers_config(servers_json_arg):
    """
    Load server configuration from argument, environment variable, or file.
    Supports both GitHub Actions (env vars) and local execution (file paths).
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


def update_node_token(server, new_token):
    """Connect to a server and update the WebSSH PM2 process with new token."""
    host = server.get("host")
    port = int(server.get("port", 22))
    username = server.get("username")
    password = server.get("password")
    work_path = server.get("path", "~")  # Default to home directory
    
    print(f"\n{'='*60}")
    print(f"Updating: {username}@{host}:{port}")
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
            return True  # Not an error, just not running
        
        process_name = webssh_process.get('name')
        pm_id = webssh_process.get('pm_id')
        print(f"  → Found process: {process_name} (ID: {pm_id})")
        
        # Step 3: Extract current arguments
        pm2_env = webssh_process.get('pm2_env', {})
        args = pm2_env.get('args', [])
        
        if isinstance(args, str):
            args = args.split()
        
        print(f"  → Current args: {' '.join(args)}")
        
        # Step 4: Replace --secret argument
        new_args = []
        secret_found = False
        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith('--secret='):
                new_args.append(f'--secret={new_token}')
                secret_found = True
                i += 1
            elif arg == '--secret':
                new_args.append('--secret')
                new_args.append(new_token)
                secret_found = True
                i += 2  # Skip the next argument (old token value)
            else:
                new_args.append(arg)
                i += 1
        
        if not secret_found:
            print("  ⚠ No --secret found in arguments, adding it")
            new_args.append(f'--secret={new_token}')
        
        new_args_str = ' '.join(new_args)
        print(f"  → New args: {new_args_str}")
        
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
        
        print(f"  ✓ Successfully updated and restarted")
        client.close()
        return True
        
    except paramiko.AuthenticationException:
        print(f"  ✗ Authentication failed")
        return False
    except paramiko.SSHException as e:
        print(f"  ✗ SSH error: {e}")
        return False
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False


def main():
    args = parse_args()
    new_token = args.new_token
    
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║         WebSSH Cluster Token Rotation Tool              ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print(f"\nNew Token: {new_token[:8]}... (truncated)")
    
    # Load server configuration
    servers = get_servers_config(args.servers_json)
    
    # Separate by role
    master_servers = [s for s in servers if s.get('role') == 'master']
    slave_servers = [s for s in servers if s.get('role') == 'slave']
    other_servers = [s for s in servers if s.get('role') not in ['master', 'slave']]
    
    print(f"\nFound {len(servers)} server(s) to update")
    if master_servers:
        print(f"  - Masters: {len(master_servers)}")
    if slave_servers:
        print(f"  - Slaves: {len(slave_servers)}")
    if other_servers:
        print(f"  - Other/Unspecified: {len(other_servers)}")
    print()
    
    # Strategy: Update slaves first, then masters to minimize disruption
    ordered_servers = slave_servers + other_servers + master_servers
    
    # Update each server
    success_count = 0
    failed_servers = []
    
    for i, server in enumerate(ordered_servers, 1):
        role_label = server.get('role', 'unknown').upper()
        print(f"\n[{i}/{len(ordered_servers)}] ({role_label})", end=" ")
        success = update_node_token(server, new_token)
        
        if success:
            success_count += 1
        else:
            failed_servers.append(f"{server.get('username')}@{server.get('host')} ({role_label})")
        
        # Small delay between servers
        if i < len(ordered_servers):
            time.sleep(1)
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total servers: {len(ordered_servers)}")
    print(f"Successfully updated: {success_count}")
    print(f"Failed: {len(failed_servers)}")
    
    if failed_servers:
        print("\nFailed servers:")
        for server in failed_servers:
            print(f"  - {server}")
        sys.exit(1)
    else:
        print("\n✓ All servers updated successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()
