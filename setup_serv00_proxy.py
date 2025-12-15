import subprocess
import requests
import socket
import re
import os
import sys

# Configuration
CF_API_TOKEN = os.environ.get("CF_API_TOKEN") # Get from environment variable
BASE_DOMAIN = "serv00.us.kg"

def run_command(command):
    """Runs a shell command and returns the output."""
    try:
        # Check=True will raise an error on non-zero exit
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        # print(f"Error running command: {command}, Stderr: {e.stderr}")
        return e.stdout.strip() if e.stdout else ""

def get_web_ip():
    """Gets the public Web IP from 'devil vhost list public'."""
    print("Fetching Web IP...")
    output = run_command("devil vhost list public")
    for line in output.splitlines():
        if "web" in line and "serv00.com" in line:
            parts = line.split()
            if parts:
                return parts[0]
    print("Error: Could not find Web IP in 'devil vhost list public'")
    sys.exit(1)

def get_server_identifier():
    """Extracts the server identifier (e.g., s16) from hostname."""
    try:
        hostname = socket.gethostname() 
        match = re.match(r"(s\d+)", hostname)
        if match:
            return match.group(1)
            
        output = run_command("devil vhost list public")
        match = re.search(r"\s+(s\d+)\.serv00\.com", output)
        if match:
            return match.group(1)
    except:
        pass
    print("Warning: Could not determine server identifier. Using 's1' as fallback.")
    return "s1"

def get_cf_zone_id(domain_name):
    """Gets the Cloudflare Zone ID for the given domain."""
    print(f"Fetching Cloudflare Zone ID for {domain_name}...")
    url = "https://api.cloudflare.com/client/v4/zones"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    params = {"name": domain_name}
    
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print(f"Error fetching Zone ID: {response.text}")
        sys.exit(1)
    
    data = response.json()
    if not data["result"]:
        print(f"Error: Zone {domain_name} not found in Cloudflare account.")
        sys.exit(1)
            
    return data["result"][0]["id"]

def add_cf_dns_record(zone_id, name, content):
    """Adds an A record to Cloudflare."""
    print(f"Adding DNS record: {name} -> {content}")
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    data = {
        "type": "A",
        "name": name,
        "content": content,
        "ttl": 1,
        "proxied": False 
    }
    
    response = requests.post(url, headers=headers, json=data)
    if response.status_code != 200:
         pass # Likely already exists or permission error
    else:
        print(f"Successfully added DNS record for {name}")

def clean_existing_proxies():
    """Checks for existing 'proxy' type websites and deletes them."""
    print("Checking for existing proxy domains...")
    output = run_command("devil www list")
    
    # Matches: backup.serv00.us.kg     proxy     http://...
    # Split lines and check column 2 (Type)
    cleaned_count = 0
    for line in output.splitlines():
        parts = line.split()
        # header might be Domain Type ... so skip that
        if len(parts) >= 2 and parts[1] == "proxy":
            domain = parts[0]
            print(f"Found existing proxy domain: {domain}. Deleting...")
            try:
                # 'devil www del' asks for confirmation, so we pipe 'y' to it
                subprocess.run(f"devil www del {domain}", shell=True, check=True, input="y\n", text=True)
                cleaned_count += 1
            except:
                print(f"Failed to delete {domain}")
    
    if cleaned_count == 0:
        print("No existing proxy domains found.")

def ensure_ports_allocated(target_count=3):
    """Ensures that exactly 'target_count' ports are allocated."""
    print("Checking allocated ports...")
    # NOTE: User input showed 'devil port list' works, 'devil port list tcp' failed
    output = run_command("devil port list")
    
    existing_ports = []
    for line in output.splitlines():
        # Parsing lines like: "25796    tcp"
        parts = line.split()
        if parts and parts[0].isdigit():
            existing_ports.append(int(parts[0]))
            
    current_count = len(existing_ports)
    print(f"Found {current_count} existing ports: {existing_ports}")
    
    needed = target_count - current_count
    
    if needed > 0:
        print(f"Need {needed} more ports. Allocating...")
        for _ in range(needed):
            try:
                subprocess.run("devil port add tcp random", shell=True, check=True, stdout=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                print("Warning: Failed to add a port. Limit likely reached.")
        
        # Re-read ports
        output = run_command("devil port list")
        existing_ports = []
        for line in output.splitlines():
            parts = line.split()
            if parts and parts[0].isdigit():
                existing_ports.append(int(parts[0]))
    else:
        print("Sufficient ports already allocated.")
            
    return existing_ports[:target_count]

def setup_reverse_proxy(domain, port):
    """Configures the reverse proxy using 'devil www add'."""
    print(f"Setting up reverse proxy: {domain} -> localhost:{port}")
    command = f"devil www add {domain} proxy localhost {port}"
    try:
        # We need to capture output to know if it succeeded
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"Failed to add proxy for {domain}. Stderr: {e.stderr.strip()}")

def main():
    global CF_API_TOKEN
    if not CF_API_TOKEN:
        print("Cloudflare API Token not found in environment (CF_API_TOKEN).")
        try:
            token_input = input("Please enter your Cloudflare API Token (or press Enter to cancel): ").strip()
        except EOFError:
            token_input = ""
            
        if not token_input:
            print("Operation cancelled.")
            sys.exit(0)
        CF_API_TOKEN = token_input

    print("--- Starting Setup ---")

    # 1. Clean existing proxies
    clean_existing_proxies()

    # 2. Ensure 3 ports (NOTE: Corrected to use 'devil port list')
    ports = ensure_ports_allocated(3)
    if not ports:
         print("Error: No ports found or allocated.")
         sys.exit(1)
         
    if len(ports) < 3:
        print(f"Warning: Only have {len(ports)} ports. Proceeding with available ports.")
    print(f"Using Ports: {ports}")

    # 3. Get Web IP & Host info
    web_ip = get_web_ip()
    server_id = get_server_identifier()
    print(f"Web IP: {web_ip}, Server: {server_id}")

    # 4. Create Subdomains & Update Cloudflare
    zone_id = get_cf_zone_id(BASE_DOMAIN)
    
    # We will map the available ports to port1, port2, port3...
    mappings = []
    for i in range(min(len(ports), 3)):
        subdomain = f"port{i+1}.{server_id}.{BASE_DOMAIN}"
        port = ports[i]
        mappings.append((subdomain, port))
        add_cf_dns_record(zone_id, subdomain, web_ip)

    # 5. Setup Proxies
    for domain, port in mappings:
        setup_reverse_proxy(domain, port)

    print("\n--- Setup Complete ---")
    print("Mappings configured:")
    for domain, port in mappings:
         print(f"http://{domain} -> localhost:{port}")
    print("----------------------")

if __name__ == "__main__":
    main()
