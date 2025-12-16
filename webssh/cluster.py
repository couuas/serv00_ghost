import json
import time
import logging
import asyncio
import tornado.httpclient
import tornado.httpclient
import tornado.web
from tornado.options import options
import platform
import os
import sys

try:
    from urllib.parse import urljoin
except ImportError:
    from urlparse import urljoin

try:
    import psutil
except ImportError:
    psutil = None

class NodeManager:
    """Manages the list of registered slave nodes."""
    def __init__(self):
        self.nodes = {}  # {node_id: {data}}

    def update_node(self, node_data):
        node_id = node_data.get('node_id')
        if not node_id:
            return
        node_data['last_seen'] = time.time()
        self.nodes[node_id] = node_data
        logging.info(f"Updated node {node_id}: {node_data.get('name')}")

    def get_nodes(self):
        nodes_list = []
        now = time.time()
        for node in self.nodes.values():
            n = node.copy()
            n['is_online'] = (now - n.get('last_seen', 0)) < 30
            nodes_list.append(n)
        return nodes_list

    # Command Queue Logic
    def __init__(self):
        self.nodes = {} 
        self.command_queue = {} # {node_id: [list_of_commands]}
        self.log_buffer = {} # {node_id_pm_id: "log content"}

    def get_logs(self, node_id, pm_id):
        key = f"{node_id}_{pm_id}"
        return self.log_buffer.get(key)
    
    def save_logs(self, node_id, pm_id, content):
        key = f"{node_id}_{pm_id}"
        self.log_buffer[key] = content
        # Auto-expire logs could be added here, for now simple overwrite

    def queue_command(self, node_id, command):
        if node_id not in self.command_queue:
            self.command_queue[node_id] = []
        self.command_queue[node_id].append(command)
        logging.info(f"Queued command for {node_id}: {command}")

    def pop_commands(self, node_id):
        if node_id in self.command_queue and self.command_queue[node_id]:
            cmds = self.command_queue[node_id]
            self.command_queue[node_id] = []
            return cmds
        return []

node_manager = NodeManager()

class MasterHandler(tornado.web.RequestHandler):
    """API endpoint for Slaves to report status."""
    def check_permission(self):
        secret = self.settings.get('secret')
        if not secret:
            return True 
        auth_header = self.request.headers.get('X-Cluster-Secret')
        return auth_header == secret

    def check_xsrf_cookie(self):
        # API endpoints don't use cookies/XSRF
        return True

    def post(self):
        if not self.check_permission():
            self.set_status(403)
            self.write("Forbidden")
            return

        try:
            data = json.loads(self.request.body)
            node_manager.update_node(data)
            
            # Send back any pending commands
            commands = node_manager.pop_commands(data.get('node_id'))
            self.write({"status": "ok", "commands": commands})
        except Exception as e:
            logging.error(f"Error processing heartbeat: {e}")
            self.set_status(400)
            self.write({"error": str(e)})

class SlaveWorker:
    """Background worker for Slave nodes to report status."""
    def __init__(self, master_url, secret, node_name=None):
        self.master_url = master_url.rstrip('/') + '/api/heartbeat'
        self.secret = secret
        self.node_name = node_name or platform.node()
        self.node_id = self.node_name 
        self.http_client = tornado.httpclient.AsyncHTTPClient()

    async def collect_stats(self):
        stats = {
            'cpu': 0.0,
            'ram_usage': 0,
            'ram_total': 0,
            'disk_usage': 0,
            'processes': 0,
            'pm2': []
        }
        
        # Collect PM2 Processes
        try:
             # User reported 'pm2' missing, needs 'npx pm2' and confirms 'y'
             # Using -y to auto-confirm npx prompts
             proc = await asyncio.create_subprocess_shell(
                'npx -y pm2 jlist',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
             stdout, stderr = await proc.communicate()
             if proc.returncode == 0:
                 pm2_data = json.loads(stdout.decode())
                 # Simplify data to minimize payload
                 for p in pm2_data:
                     stats['pm2'].append({
                         'id': p.get('pm_id'),
                         'name': p.get('name'),
                         'status': p.get('pm2_env', {}).get('status'),
                         'memory': p.get('monit', {}).get('memory', 0),
                         'cpu': p.get('monit', {}).get('cpu', 0),
                         'uptime': p.get('pm2_env', {}).get('pm_uptime', 0)
                     })
        except Exception:
             pass # PM2 likely not installed or fails, ignore silently
        
        if psutil:
            logging.info("Collecting stats with psutil")
            try:
                stats['cpu'] = psutil.cpu_percent(interval=None)
            except Exception as e:
                logging.error(f"CPU Stats Error: {e}")

            try:
                mem = psutil.virtual_memory()
                stats['ram_usage'] = mem.used
                stats['ram_total'] = mem.total
            except Exception as e:
                logging.error(f"RAM Stats Error: {e}")
                
            try:
                du = psutil.disk_usage('/')
                stats['disk_usage'] = du.percent
            except Exception as e:
                logging.error(f"Disk Stats Error: {e}")
                
            try:
                stats['processes'] = len(psutil.pids())
            except Exception as e:
                logging.error(f"Proc Stats Error: {e}")
        else:
            logging.info("psutil missing, using fallback")
            if hasattr(os, 'getloadavg'):
                try:
                    stats['cpu'] = os.getloadavg()[0]
                except:
                    pass
        
        return stats

    async def send_heartbeat(self):
        stats = await self.collect_stats()
        
        my_url = options.external_url
        if not my_url:
            my_url = 'http://{}:{}'.format('127.0.0.1', options.port) # Fallback

        # Generate SSH Link
        ssh_link = my_url
        if options.ssh_user and options.ssh_password:
             import base64
             try:
                 # WebSSH expects base64 encoded password
                 b64_pass = base64.b64encode(options.ssh_password.encode('utf-8')).decode('utf-8')
                 from urllib.parse import urlencode, urlparse, urlunparse
                 
                 # Ensure my_url is base
                 parsed = urlparse(my_url)
                 
                 # Prepare query params
                 query = {
                     'hostname': options.ssh_host,
                     'username': options.ssh_user,
                     'password': b64_pass,
                     'port': options.ssh_port
                 }
                 # Append secret if exists (to pass auth check)
                 if self.secret:
                     query['secret'] = self.secret
                     
                 # Construct full URL with params
                 # We simply append query string
                 new_query = urlencode(query)
                 if parsed.query:
                     new_query = parsed.query + '&' + new_query
                 
                 ssh_link = urlunparse((
                     parsed.scheme, parsed.netloc, parsed.path, 
                     parsed.params, new_query, parsed.fragment
                 ))
             except Exception as e:
                 logging.error(f"Failed to generate SSH link: {e}")



        data = {
            'node_id': self.node_id,
            'name': self.node_name,
            'url': ssh_link, # Use the smart link as the main URL
            'stats': stats,
            'username': options.ssh_user
        }
        
        headers = {'Content-Type': 'application/json'}
        if self.secret:
            headers['X-Cluster-Secret'] = self.secret

        try:
            response = await self.http_client.fetch(
                self.master_url,
                method='POST',
                body=json.dumps(data),
                headers=headers
            )
            logging.debug("Heartbeat sent successfully")
            
            # Process response commands
            if response.body:
                resp_data = json.loads(response.body)
                commands = resp_data.get('commands', [])
                for cmd in commands:
                    await self.execute_remote_command(cmd)
                    
        except Exception as e:
            logging.error(f"Failed to send heartbeat to {self.master_url}: {e}")

    async def execute_remote_command(self, cmd_data):
        """Execute command received from Master"""
        action = cmd_data.get('action') # start, stop, restart, delete, logs
        pm_id = cmd_data.get('pm_id')
        
        if action == 'logs':
             logging.info(f"Fetching logs for {pm_id}")
             try:
                 # Fetch headers/tails of logs
                 cmd = f'npx -y pm2 logs {pm_id} --lines 50 --nostream'
                 proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                 )
                 stdout, stderr = await proc.communicate()
                 
                 # Send back to Master
                 log_content = stdout.decode('utf-8') + stderr.decode('utf-8')
                 await self.report_logs(pm_id, log_content)
             except Exception as e:
                 logging.error(f"Error fetching logs: {e}")

        elif action in ['start', 'stop', 'restart', 'delete'] and pm_id is not None:
            logging.info(f"Executing remote command: {action} {pm_id}")
            cmd = f'npx -y pm2 {action} {pm_id}'
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.communicate()
            except Exception as e:
                logging.error(f"Error executing {cmd}: {e}")

    async def report_logs(self, pm_id, content):
        url = self.master_url.replace('/heartbeat', '/callback/logs')
        data = {
            'node_id': self.node_id,
            'pm_id': pm_id,
            'content': content
        }
        headers = {'Content-Type': 'application/json'}
        if self.secret:
             headers['X-Cluster-Secret'] = self.secret
        try:
             await self.http_client.fetch(url, method='POST', body=json.dumps(data), headers=headers)
        except Exception as e:
             logging.error(f"Failed to upload logs: {e}")

    def start(self):
        logging.info(f"Starting SlaveWorker connecting to {self.master_url}")
        tornado.ioloop.PeriodicCallback(self.send_heartbeat, 10000).start()


class BaseAuthHandler(tornado.web.RequestHandler):
    def check_auth(self):
        auth_password = options.auth_password
        if not auth_password:
            return True
        
        # Check Cookie 
        cookie_auth = self.get_cookie('auth_token')
        if cookie_auth == auth_password:
            return True
            
        auth_header = self.request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Basic '):
            return False
            
        import base64
        try:
            auth_decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
            username, password = auth_decoded.split(':', 1)
            return password == auth_password
        except:
            return False

    def request_auth(self):
        self.set_header('WWW-Authenticate', 'Basic realm="Cluster Master"')
        self.set_status(401)
        self.finish()
        return False

class LoginHandler(tornado.web.RequestHandler):
    def post(self):
        try:
            data = json.loads(self.request.body)
            password = data.get('password')
            if password == options.auth_password:
                self.set_cookie('auth_token', password, expires_days=7)
                self.write({"status": "ok"})
            else:
                self.set_status(403)
                self.write({"error": "Invalid password"})
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})

class DashboardHandler(BaseAuthHandler):
    def get(self):
        # Allow Guest Access (secret is empty if not authed)
        is_authed = self.check_auth()
        secret = options.secret if is_authed else ''
        self.render('dashboard.html', secret=secret, is_authed=is_authed)

class NodeListHandler(BaseAuthHandler):
    def get(self):
        # Allow Guest Access to see list
        self.write(json.dumps(node_manager.get_nodes()))

class NodeControlHandler(BaseAuthHandler):
    """API to control remote nodes (send commands)."""
    def post(self):
        if not self.check_auth():
            self.set_status(403)
            return

        try:
            data = json.loads(self.request.body)
            node_id = data.get('node_id')
            action = data.get('action') # start, stop, restart, delete
            pm_id = data.get('pm_id')

            if not node_id or not action:
                self.set_status(400)
                self.write({'error': 'Missing node_id or action'})
                return

            # Queue command for the slave to pick up on next heartbeat
            command = {
                'action': action,
                'pm_id': pm_id
            }
            node_manager.queue_command(node_id, command)
            
            logging.info(f"Admin queued command {action} for node {node_id}")
            self.write({'status': 'ok', 'message': 'Command queued'})
            
        except Exception as e:
            self.set_status(400)
            self.write({'error': str(e)})

class LogCallbackHandler(MasterHandler):
    """Slave posts logs here."""
    def post(self):
        if not self.check_permission():
            self.set_status(403); return
        try:
            data = json.loads(self.request.body)
            node_manager.save_logs(data.get('node_id'), data.get('pm_id'), data.get('content'))
            self.write({"status": "ok"})
        except:
            self.set_status(400)

class LogViewHandler(BaseAuthHandler):
    """Frontend gets logs here."""
    def get(self):
        if not self.check_auth(): self.set_status(403); return
        node_id = self.get_argument('node_id')
        pm_id = self.get_argument('pm_id')
        logs = node_manager.get_logs(node_id, pm_id)
        self.write({"logs": logs if logs else "WAITING_FOR_DATA..."})
