import json
import os
import sys
import datetime
import requests
import paramiko

def send_telegram_message(token, chat_id, message):
    if not token or not chat_id:
        print("::warning::Telegram token or chat ID missing. Skipping notification.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "MarkdownV2"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print("Telegram notification sent successfully.")
    except Exception as e:
        print(f"::error::Failed to send Telegram message: {e}")

def load_config():
    """
    Load SERVERS_JSON and TELEGRAM_JSON from environment or file.
    Supports both GitHub Actions (env vars) and local execution (file paths).
    """
    # Load SERVERS_JSON
    servers_json = os.environ.get("SERVERS_JSON")
    servers_file = os.environ.get("SERVERS_FILE", "servers.json")
    
    if servers_json:
        try:
            servers = json.loads(servers_json)
        except json.JSONDecodeError as e:
            print(f"::error::Failed to parse SERVERS_JSON: {e}")
            sys.exit(1)
    elif os.path.isfile(servers_file):
        print(f"Loading servers from file: {servers_file}")
        try:
            with open(servers_file, 'r') as f:
                servers = json.load(f)
        except Exception as e:
            print(f"::error::Failed to read {servers_file}: {e}")
            sys.exit(1)
    else:
        print("::error::SERVERS_JSON or SERVERS_FILE not found.")
        sys.exit(1)

    # Load TELEGRAM_JSON
    telegram_json = os.environ.get("TELEGRAM_JSON")
    telegram_file = os.environ.get("TELEGRAM_FILE", "telegram.json")
    telegram_config = {}
    
    if telegram_json:
        try:
            telegram_config = json.loads(telegram_json)
        except json.JSONDecodeError as e:
            print(f"::warning::Failed to parse TELEGRAM_JSON: {e}")
    elif os.path.isfile(telegram_file):
        print(f"Loading Telegram config from file: {telegram_file}")
        try:
            with open(telegram_file, 'r') as f:
                telegram_config = json.load(f)
        except Exception as e:
            print(f"::warning::Failed to read {telegram_file}: {e}")
    
    return servers, telegram_config

def main():
    # 1. Load Configurations
    servers, telegram_config = load_config()
    
    # 2. Separate servers by role (optional, for future use)
    master_servers = [s for s in servers if s.get('role') == 'master']
    slave_servers = [s for s in servers if s.get('role') == 'slave']
    other_servers = [s for s in servers if s.get('role') not in ['master', 'slave']]
    
    print(f"Starting Health Check for {len(servers)} servers...")
    if master_servers:
        print(f"  - Masters: {len(master_servers)}")
    if slave_servers:
        print(f"  - Slaves: {len(slave_servers)}")
    if other_servers:
        print(f"  - Other/Unspecified: {len(other_servers)}")
    print()

    # 3. Perform Checks
    results = []
    success_count = 0

    for server in servers:
        host = server.get("host")
        port = int(server.get("port", 22))
        username = server.get("username")
        password = server.get("password")
        
        print(f"Checking {username}@{host}:{port} ...")
        
        status = "❌ Failed"
        details = ""
        is_online = False
        
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=host, port=port, username=username, password=password, timeout=10)
            
            # Run a simple echo command
            stdin, stdout, stderr = client.exec_command("echo 'OK'")
            exit_code = stdout.channel.recv_exit_status()
            
            if exit_code == 0:
                status = "✅ Online"
                is_online = True
                success_count += 1
            else:
                status = "⚠️ Error"
                details = stderr.read().decode().strip()
            
            client.close()
        except Exception as e:
            details = str(e)
            # Shorten error message
            if "Authentication failed" in details:
                status = "🔐 Auth Failed"
            elif "timed out" in details:
                status = "⏱️ Timeout"
            elif "Connection refused" in details:
                status = "🚫 Refused"
        
        results.append({
            "host": host,
            "username": username,
            "status": status,
            "details": details,
            "is_online": is_online
        })

    # 3. Generate Reports
    
    # GitHub Step Summary (Markdown)
    summary_md = "## 🏥 Server Health Report\n\n"
    summary_md += f"**Time:** {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
    summary_md += f"**Total:** {len(servers)} | **Online:** {success_count} | **Offline:** {len(servers) - success_count}\n\n"
    summary_md += "| Host | User | Status | Details |\n"
    summary_md += "|------|------|--------|---------|\n"
    
    for r in results:
        details_clean = r['details'].replace('\n', ' ') if r['details'] else ''
        summary_md += f"| `{r['host']}` | `{r['username']}` | {r['status']} | {details_clean} |\n"

    github_step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_step_summary:
        with open(github_step_summary, "a", encoding="utf-8") as f:
            f.write(summary_md)
    else:
        print("\n--- Summary ---")
        print(summary_md)

    # Telegram Notification
    tg_token = telegram_config.get("telegramToken")
    tg_chat_id = telegram_config.get("telegramChatId")

    if tg_token and tg_chat_id:
        # Build Telegram Message
        current_time = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        
        # Header (MarkdownV2 escaping needed for non-code blocks)
        def escape_md2(text):
            escape_chars = r"_*[]()~`>#+-=|{}.!"
            return "".join(f"\\{c}" if c in escape_chars else c for c in text)

        header = f"📢 *Server Health Check*\n"
        header += f"⏰ Time: `{escape_md2(current_time)}` \\(CST\\)\n"
        header += f"📊 Status: {success_count}/{len(servers)} Online\n"

        # Content in Code Block for Alignment
        # Calculate max length of username@host for padding
        max_len = 0
        rows = []
        for r in results:
            user_host = f"{r['username']}@{r['host']}"
            max_len = max(max_len, len(user_host))
            # Determine status symbol/text
            if r['is_online']:
                status_short = "✅ OK"
            else:
                status_short = "❌ FAIL"
            rows.append((user_host, status_short, r))

        # Build clean table in a code block
        table_lines = []
        for user_host, status_short, r in rows:
            # Pad host to align status to the right
            # e.g "user@host      ... ✅ OK"
            padding = " " * (max_len - len(user_host) + 2)
            line = f"{user_host}{padding}{status_short}"
            table_lines.append(line)
        
        table_str = "\n".join(table_lines)
        
        # Combine: Header + Code Block
        # ```
        # user@host  ✅ OK
        # ```
        tg_msg_v2 = f"{header}```text\n{table_str}\n```"
        
        # Append error details outside code block if any
        has_errors = False
        for r in results:
            if not r['is_online'] and r['details']:
                if not has_errors:
                    tg_msg_v2 += "\n*Errors:*\n"
                    has_errors = True
                
                clean_host = escape_md2(f"{r['username']}@{r['host']}")
                clean_err = escape_md2(r['details'][:100])
                tg_msg_v2 += f"🔴 {clean_host}: `{clean_err}`\n"

        send_telegram_message(tg_token, tg_chat_id, tg_msg_v2)

    if success_count != len(servers):
        sys.exit(1)

if __name__ == "__main__":
    # Fix for Windows console encoding
    if sys.platform.startswith('win'):
        sys.stdout.reconfigure(encoding='utf-8')
    main()
