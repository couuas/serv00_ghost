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
            # Create a copy to avoid mutating the original stored data permanently for display
            n = node.copy()
            # Calculate status based on server time
            # 30 seconds threshold for "online" (heartbeat is 10s)
            n['is_online'] = (now - n.get('last_seen', 0)) < 30
            nodes_list.append(n)
        return nodes_list

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
            self.write({"status": "ok"})
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

        # Extract season from ssh_host (e.g. s1.serv00.com -> s1)
        season = 'Unknown'
        if options.ssh_host:
            import re
            match = re.match(r'(s\d+)', options.ssh_host)
            if match:
                season = match.group(1)
            else:
                season = options.ssh_host.split('.')[0]

        data = {
            'node_id': self.node_id,
            'name': self.node_name,
            'url': ssh_link, # Use the smart link as the main URL
            'stats': stats,
            'username': options.ssh_user,
            'season': season
        }
        
        headers = {'Content-Type': 'application/json'}
        if self.secret:
            headers['X-Cluster-Secret'] = self.secret

        try:
            await self.http_client.fetch(
                self.master_url,
                method='POST',
                body=json.dumps(data),
                headers=headers
            )
            logging.debug("Heartbeat sent successfully")
        except Exception as e:
            logging.error(f"Failed to send heartbeat to {self.master_url}: {e}")

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
