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

def main():
    # 1. Load Configurations
    servers_json = os.environ.get("SERVERS_JSON")
    telegram_json = os.environ.get("TELEGRAM_JSON")
    
    if not servers_json:
        print("::error::SERVERS_JSON secret is missing or empty.")
        sys.exit(1)

    try:
        servers = json.loads(servers_json)
    except json.JSONDecodeError as e:
        print(f"::error::Failed to parse SERVERS_JSON: {e}")
        sys.exit(1)

    telegram_config = {}
    if telegram_json:
        try:
            telegram_config = json.loads(telegram_json)
        except json.JSONDecodeError as e:
            print(f"::warning::Failed to parse TELEGRAM_JSON: {e}")

    # 2. Perform Checks
    print(f"Starting Health Check for {len(servers)} servers...")
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
    # Escape special characters for MarkdownV2
    # We will build a cleaner message for Telegram
    tg_token = telegram_config.get("telegramToken")
    tg_chat_id = telegram_config.get("telegramChatId")

    if tg_token and tg_chat_id:
        # Build Telegram Message
        # Header
        current_time = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        tg_msg = f"📢 *Server Health Check*  \n"
        tg_msg += f"⏰ Time: `{current_time}` (CST)\n"
        tg_msg += f"📊 Status: {success_count}/{len(servers)} Online\n\n"
        
        for r in results:
            # Escape necessary characters: _ * [ ] ( ) ~ ` > # + - = | { } . !
            # Or just use simple formatting
             # Simple approach: Usage of monospace for host/user
            user_host = f"{r['username']}@{r['host']}"
            status_icon = "🟢" if r['is_online'] else "🔴"
            # Only show failed details if offline
            detail_txt = f" \n   Reason: `{r['details']}`" if not r['is_online'] and r['details'] else ""
            
            tg_msg += f"{status_icon} `{user_host}`: {r['status']}{detail_txt}\n"

        # Escape reserved characters for MarkdownV2 if needed, or use 'Markdown' mode?
        # The script uses MarkdownV2 in the function.
        # MarkdownV2 requires escaping: _ * [ ] ( ) ~ ` > # + - = | { } . !
        # It is safer/easier to use 'HTML' or just 'Markdown' (legacy) if we want less strict escaping,
        # OR just handle the escaping. 
        # Let's try to do basic escaping for MarkdownV2.
        
        def escape_md2(text):
            escape_chars = r"_*[]()~`>#+-=|{}.!"
            return "".join(f"\\{c}" if c in escape_chars else c for c in text)

        # Re-build strictly for MarkdownV2 to avoid errors
        tg_msg_v2 = f"📢 *Server Health Check*\n"
        tg_msg_v2 += f"⏰ Time: `{escape_md2(current_time)}` \\(CST\\)\n"
        tg_msg_v2 += f"📊 Status: {success_count}/{len(servers)} Online\n\n"
        
        for r in results:
            user_host = escape_md2(f"{r['username']}@{r['host']}")
            status_text = escape_md2(r['status'])
            status_icon = "🟢" if r['is_online'] else "🔴"
            
            tg_msg_v2 += f"{status_icon} `{user_host}`: {status_text}\n"
            if not r['is_online'] and r['details']:
                err_detail = escape_md2(r['details'][:50]) # limit length
                tg_msg_v2 += f"   Reason: `{err_detail}`\n"

        send_telegram_message(tg_token, tg_chat_id, tg_msg_v2)

    if success_count != len(servers):
        sys.exit(1)

if __name__ == "__main__":
    main()
