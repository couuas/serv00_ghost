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

from webssh.handler import IndexHandler

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
        self.node_apps = {} # {node_id: [app_list]}

    def get_nodes(self):
        # Merge static node list (if any), active nodes from heartbeat, and apps
        # For now simpler approach: return self.nodes, injected with apps
        nodes_list = []
        now = time.time()
        for node_id, info in self.nodes.items():
            # Inject apps if available
            info['stats']['pm2'] = self.node_apps.get(node_id, [])
            # Server-side online check (30s threshold)
            # info['last_seen'] is set by Master on receipt
            info['is_online'] = (now - info.get('last_seen', 0)) < 30
            nodes_list.append(info)
        return nodes_list
    
    def update_apps(self, node_id, apps):
        self.node_apps[node_id] = apps

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
            'processes': 0
        }
        
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
        action = cmd_data.get('action') # start, stop, restart, delete, logs, list_apps
        pm_id = cmd_data.get('pm_id')
        
        if action == 'list_apps':
             logging.info("Fetching App List (On-Demand)")
             try:
                 cmd = 'npx -y pm2 jlist'
                 proc = await asyncio.create_subprocess_shell(
                    cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                 )
                 stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
                 if proc.returncode == 0:
                     pm2_data = json.loads(stdout.decode())
                     apps = []
                     for p in pm2_data:
                         apps.append({
                             'id': p.get('pm_id'),
                             'name': p.get('name'),
                             'status': p.get('pm2_env', {}).get('status'),
                             'memory': p.get('monit', {}).get('memory', 0),
                             'cpu': p.get('monit', {}).get('cpu', 0),
                             'uptime': p.get('pm2_env', {}).get('pm_uptime', 0)
                         })
                     await self.report_apps(apps)
                 else:
                     logging.error(f"List Apps Failed: {stderr.decode()}")
             except Exception as e:
                 import traceback
                 logging.error(f"List Apps Error: {e}")
                 logging.error(traceback.format_exc())

        elif action == 'logs':
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

    async def report_apps(self, apps):
        url = self.master_url.replace('/heartbeat', '/callback/apps')
        data = {'node_id': self.node_id, 'apps': apps}
        headers = {'Content-Type': 'application/json'}
        if self.secret: headers['X-Cluster-Secret'] = self.secret
        try: await self.http_client.fetch(url, method='POST', body=json.dumps(data), headers=headers)
        except Exception as e: logging.error(f"Failed to upload apps: {e}")

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


class RateLimiter:
    _failures = {} # {ip: {'count': int, 'reset': timestamp}}
    _lockouts = {} # {ip: unlock_timestamp}

    @classmethod
    def check(cls, ip):
        import time
        now = time.time()
        
        # Check lockout
        if ip in cls._lockouts:
            unlock_time = cls._lockouts[ip]
            if now < unlock_time:
                return False, int(unlock_time - now)
            else:
                del cls._lockouts[ip]
                # Reset failures after lockout expires
                if ip in cls._failures: del cls._failures[ip]

        return True, 0

    @classmethod
    def record_failure(cls, ip):
        import time
        now = time.time()
        
        # Init or Reset window if passed (5 minutes window)
        if ip not in cls._failures or (now - cls._failures[ip]['reset'] > 300):
            cls._failures[ip] = {'count': 1, 'reset': now}
        else:
             cls._failures[ip]['count'] += 1

        # Check threshold (5 attempts)
        if cls._failures[ip]['count'] >= 5:
            cls._lockouts[ip] = now + 900 # 15 minutes lockout
            logging.warning(f"IP {ip} banned for brute force attempts")
            return True # Is now locked out
            
        return False # Still allowed

class LoginHandler(tornado.web.RequestHandler):
    def post(self):
        try:
            ip = self.request.remote_ip
            allowed, wait_time = RateLimiter.check(ip)
            if not allowed:
                 self.set_status(429)
                 self.write({"error": f"Too many attempts. Try again in {wait_time}s."})
                 return

            data = json.loads(self.request.body)
            password = data.get('password')
            
            if password == options.auth_password:
                # Clear failures on success
                if ip in RateLimiter._failures: del RateLimiter._failures[ip]
                
                self.set_cookie('auth_token', password, expires_days=7)
                self.write({"status": "ok"})
            else:
                RateLimiter.record_failure(ip)
                # Add slight delay to slow down brute force
                import time
                time.sleep(1) 
                
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

class AppListCallbackHandler(MasterHandler):
    """Slave posts app list here."""
    def post(self):
        if not self.check_permission():
            self.set_status(403); return
        try:
            data = json.loads(self.request.body)
            node_manager.update_apps(data.get('node_id'), data.get('apps'))
            self.write({"status": "ok"})
        except:
            self.set_status(400)

class SlavePM2APIHandler(MasterHandler):
    """
    RUNS ON SLAVE: Exposes PM2 functionality via HTTP API.
    Protected by X-Cluster-Secret checks (inherited from MasterHandler check_permission).
    """
    async def post(self):
        # MasterHandler check_permission checks X-Cluster-Secret matches settings.secret
        if not self.check_permission():
            self.set_status(403)
            return

        try:
            data = json.loads(self.request.body)
            action = data.get('action') # list, start, stop, restart, logs, delete
            pm_id = data.get('pm_id')

            if action == 'list':
                cmd = 'npx -y pm2 jlist'
                proc = await asyncio.create_subprocess_shell(
                    cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180.0)
                if proc.returncode == 0:
                    raw_data = json.loads(stdout.decode())
                    apps = []
                    for p in raw_data:
                        apps.append({
                            'id': p.get('pm_id'),
                            'name': p.get('name'),
                            'mode': p.get('pm2_env', {}).get('exec_mode'),
                            'pid': p.get('pid'),
                            'status': p.get('pm2_env', {}).get('status'),
                            'memory': p.get('monit', {}).get('memory', 0),
                            'cpu': p.get('monit', {}).get('cpu', 0),
                            'uptime': p.get('pm2_env', {}).get('pm_uptime', 0)
                        })
                    self.write({'success': True, 'data': apps})
                else:
                    self.write({'success': False, 'error': stderr.decode()})
            
            elif action == 'logs':
                cmd = f'npx -y pm2 logs {pm_id} --lines 100 --nostream'
                proc = await asyncio.create_subprocess_shell(
                    cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180.0)
                if proc.returncode == 0:
                    self.write({'success': True, 'logs': stdout.decode()})
                else:
                    self.write({'success': False, 'error': stderr.decode()})

            elif action in ['start', 'stop', 'restart', 'delete']:
                cmd = f'npx -y pm2 {action} {pm_id}'
                proc = await asyncio.create_subprocess_shell(
                    cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180.0)
                if proc.returncode == 0:
                     self.write({'success': True, 'message': f'{action} successful'})
                else:
                     self.write({'success': False, 'error': stderr.decode()})
            
            else:
                self.set_status(400)
                self.write({'success': False, 'error': 'Invalid action'})

        except Exception as e:
            logging.error(f"Slave API Error: {e}")
            self.set_status(500)
            self.write({'success': False, 'error': str(e)})


class MasterAppsProxyHandler(BaseAuthHandler):
    """
    RUNS ON MASTER: Proxies requests from Apps UI to the target Slave API.
    """
    def check_xsrf_cookie(self):
        return True

    async def post(self):
        if not self.check_auth():
            self.set_status(403); return

        try:
            import platform
            import tornado.httpclient
            from urllib.parse import urlparse
            body = json.loads(self.request.body)
            node_id = body.get('node_id')
            
            # Lookup Node URL
            # We iterate node_manager.nodes to find matching ID
            target_url = None
            if node_id == 'local' or node_id == platform.node():
                 # Handle Master Local Case? 
                 # If Master itself runs SlaveWorker logic, it has a separate port? 
                 # Or we execute locally?
                 # For simplicity, if architecture is Master -> Slave (HTTP), 
                 # Master should also run the SlavePM2APILogic if it wants to be managed.
                 # Assuming all nodes register via heartbeat including Master-Local.
                 pass
            
            node_info = node_manager.nodes.get(node_id)

            if not node_info:
                self.set_status(404)
                self.write({'error': 'Node not found or offline'})
                return

            target_url = node_info.get('url') # e.g. http://slave:8888?query=...
            
            # Construct Slave API URL
            # target_url usually comes from heartbeat external_url or constructed.
            # It might contain query params (ssh credentials). We need base.
            parsed = urlparse(target_url)
            # Reconstruct clean base URL without query strings
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
            
            api_url = f"{clean_url}/api/slave/pm2"
            
            # Forward Request
            http_client = tornado.httpclient.AsyncHTTPClient()
            headers = {'Content-Type': 'application/json'}
            if options.secret:
                headers['X-Cluster-Secret'] = options.secret
                
            response = await http_client.fetch(
                api_url, 
                method='POST', 
                body=json.dumps(body), 
                headers=headers,
                request_timeout=180.0,
                raise_error=False # Don't raise exception on 4xx/5xx, handle manually
            )
            
            # Relay Response
            self.set_status(response.code)
            # Check if response is JSON
            try:
                json_body = json.loads(response.body)
                self.write(json_body)
            except:
                # If HTML or text, wrap in JSON error to prevent frontend crash
                logging.error(f"Proxy received non-JSON from Slave ({api_url}): {response.body[:100]}")
                self.write({'error': f'Slave Error ({response.code}): {response.body.decode()[:200]}'})

        except Exception as e:
            logging.error(f"Proxy Error: {e}")
            self.set_status(500)
            self.write({'error': str(e)})

        except tornado.httpclient.HTTPError as e:
             self.set_status(e.code)
             if e.response: self.write(e.response.body)
             else: self.write({'error': str(e)})
        except Exception as e:
            logging.error(f"Proxy Error: {e}")
            self.set_status(500)
            self.write({'error': str(e)})

class AppsPageHandler(BaseAuthHandler):
    def get(self, node_id):
        if not self.check_auth():
            self.redirect('/')
            return
        self.render('apps.html', node_id=node_id)

class LogViewHandler(BaseAuthHandler):
    """Frontend gets logs here."""
    def get(self):
        if not self.check_auth(): self.set_status(403); return
        node_id = self.get_argument('node_id')
        pm_id = self.get_argument('pm_id')
        logs = node_manager.get_logs(node_id, pm_id)
        self.write({"logs": logs if logs else "WAITING_FOR_DATA..."})

class SlaveIndexHandler(IndexHandler):
    """
    Wraps IndexHandler to protect the root / access on Slaves.
    If 'secret' query param doesn't match, show alert.html.
    """
    def get(self):
        # 1. Check Secret
        secret = self.get_argument('secret', '')
        # Also check header just in case, though usually browser visit
        if not secret:
             secret = self.request.headers.get('X-Cluster-Secret', '')

        if options.secret and secret != options.secret:
            # UNAUTHORIZED -> Show Alert
            self.set_status(403)
            self.render('alert.html')
            return

        # 2. If authorized, proceed with normal WebSSH Page
        super(SlaveIndexHandler, self).get()
