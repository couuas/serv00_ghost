import json
import os
import asyncio
import logging
import datetime
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from webssh.cluster import node_manager

class AccountManager:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self._ensure_settings()

    def _ensure_settings(self):
        if not os.path.exists(self.settings_file):
            with open(self.settings_file, 'w') as f:
                json.dump({}, f)

    def get_accounts(self):
        """
        Retrieves accounts from active slave nodes.
        Returns a dict: {username: {'user': username, 'season': 'Auto'}}
        """
        nodes = node_manager.get_nodes()
        accounts = {}
        for node in nodes:
            # Try to get username from 'username' field (sent by slave)
            # Fallback to 'name' if not present
            user = node.get('username') or node.get('name')
            if user:
                accounts[user] = {
                    "user": user, 
                    "season": node.get('season', 'Auto')
                }
        return accounts

    def get_settings(self):
        try:
            with open(self.settings_file, 'r') as f:
                return json.load(f)
        except Exception:
            return {}

    def save_settings(self, settings):
        current = self.get_settings()
        current.update(settings)
        with open(self.settings_file, 'w') as f:
            json.dump(current, f, indent=2)


class StatusChecker:
    @staticmethod
    async def check_account(user):
        url = f"https://{user}.serv00.net/"
        client = AsyncHTTPClient()
        try:
            # First check: Direct access
            req = HTTPRequest(url, method="GET", validate_cert=False, follow_redirects=False, request_timeout=10)
            response = await client.fetch(req, raise_error=False)
            
            if response.code == 200:
                return "⭕ 状态正常"
            elif response.code in [301, 302, 403]:
                # Second check: External API for blocked/unregistered status
                return await StatusChecker._check_external_status(user)
            else:
                return f"⚠️ 响应: {response.code}"
        except Exception as e:
            logging.error(f"Check failed for {user}: {e}")
            return "❌ 检测失败"

    @staticmethod
    async def _check_external_status(user):
        api_url = f"https://serv00.eooce.com/check-serv00?username={user}"
        client = AsyncHTTPClient()
        try:
            response = await client.fetch(api_url, request_timeout=10)
            data = json.loads(response.body)
            if data.get("type") == "active":
                return "⭕ 状态正常"
            else:
                return "❌ 已被封禁"
        except Exception:
             return "🔍 未注册/被封"

class TelegramBot:
    @staticmethod
    async def send_message(token, chat_id, text):
        if not token or not chat_id:
            return
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "MarkdownV2"
        }
        
        client = AsyncHTTPClient()
        try:
            headers = {"Content-Type": "application/json"}
            req = HTTPRequest(url, method="POST", body=json.dumps(payload), headers=headers)
            await client.fetch(req)
        except Exception as e:
            logging.error(f"Telegram send failed: {e}")

class Scheduler:
    def __init__(self, manager, checker, bot_class):
        self.manager = manager
        self.checker = checker
        self.bot_class = bot_class
        self._callback = None
        self.running = False

    def start(self):
        self.stop()
        settings = self.manager.get_settings()
        interval = int(settings.get("timeValue", 0))
        
        if interval > 0 and settings.get("scheduleType") == 'interval':
            self.running = True
            ms = interval * 60 * 1000
            from tornado.ioloop import PeriodicCallback
            self._callback = PeriodicCallback(self.run_job, ms)
            self._callback.start()
            logging.info(f"Monitor Scheduler started. Interval: {interval} minutes")

    def stop(self):
        if self._callback:
            self._callback.stop()
            self._callback = None
        self.running = False

    async def run_job(self):
        logging.info("Running scheduled account check...")
        accounts = self.manager.get_accounts()
        if not accounts:
            return

        results = {}
        for user in accounts:
            status = await self.checker.check_account(user)
            results[user] = status

        settings = self.manager.get_settings()
        token = settings.get("telegramToken")
        chat_id = settings.get("telegramChatId")
        
        if token and chat_id:
            msg_lines = ["📢 **账号检测结果**："]
            for user, status in results.items():
                msg_lines.append(f"`{user}` : {status}")
            
            beijing_time = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
            msg_lines.append(f"\n⏰ 北京时间：{beijing_time}")
            
            await self.bot_class.send_message(token, chat_id, "\n".join(msg_lines))

class StatusPersistence:
    def __init__(self, filename="account_status.json"):
        self.filename = filename

    def save_status(self, data):
        try:
            with open(self.filename, 'w') as f:
                json.dump(data, f, indent=2)
            logging.info(f"Saved account status to {self.filename}")
        except Exception as e:
            logging.error(f"Failed to save account status: {e}")

    def load_status(self):
        if not os.path.exists(self.filename):
            return {}
        try:
            with open(self.filename, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load account status: {e}")
            return {}

monitor_manager = AccountManager()
monitor_scheduler = Scheduler(monitor_manager, StatusChecker, TelegramBot)
status_persistence = StatusPersistence()
