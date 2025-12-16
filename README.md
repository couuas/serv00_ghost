# WebSSH Cluster Manager

![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/couuas/webssh/deploy.yml?label=Deploy)
![Python](https://img.shields.io/badge/python-3.7+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)


An advanced WebSSH solution capable of managing multiple servers (Serv00 nodes) in a distributed Master/Slave architecture.

## ✨ Features

- **Distributed Architecture**: Supports **Master** (Controller) and **Slave** (Worker) modes.
- **Unified Dashboard**: View status of all connected nodes (CPU, RAM, Disk, Process) in a single Master dashboard.
- **Smart Connection**: One-click SSH login to any Slave node using pre-configured credentials.
- **System Monitoring**: Real-time probe using `psutil` to monitor system resources.
- **Secure Communication**: Inter-node communication is secured with a Secret Token.
- **Easy Deployment**: One-key setup script (`setup.sh`) for quick configuration.

## 🚀 Quick Start

### 1. Requirements
- Python 3.7+
- Dependencies: `pip install -r requirements.txt`

### 2. Auto Setup (Recommended)
Run the interactive setup script to configure the node:
```bash
bash setup.sh
```
Follow the prompts to select **master**, **slave**, or **standalone** mode and configure external URLs and passwords.

---

## 🏗️ Manual Configuration

### Master Node
The Master node hosts the dashboard and manages the registry of slaves.
```bash
python run.py --mode=master \
    --port=8888 \
    --secret=YOUR_CLUSTER_SECRET \
    --auth_password=DASHBOARD_LOGIN_PASSWORD
```
- Access Dashboard: `http://localhost:8888`

### Slave Node
Slave nodes provide WebSSH access and report system stats to the Master.
```bash
python run.py --mode=slave \
    --port=8880 \
    --secret=YOUR_CLUSTER_SECRET \
    --master_url=http://MASTER_IP:8888 \
    --external_url=http://SLAVE_IP:8880 \
    --ssh_host=localhost \
    --ssh_port=22 \
    --ssh_user=root \
    --ssh_password=root_password
```

### Arguments Explained
- `--mode`: Run mode (`master`, `slave`, `standalone`). Default: `standalone`.
- `--secret`: Shared secret token for verifying slave-master communication.
- `--auth_password`: Password for accessing the Master Dashboard.
- `--master_url`: URL of the Master node (for Slaves).
- `--external_url`: The public-facing URL of *this* node (used by Master to redirect users).
- `--ssh_*`: Pre-configure SSH credentials for Smart Link (Auto-Connect).

## 📊 Dashboard
The Master Dashboard (`/`) displays:
- List of online/offline nodes.
- Real-time CPU/RAM/Disk usage.
- "Connect" button for instant WebSSH access to the node.

## 🔒 Security Note
- **Secret Token**: Ensure `--secret` is complex and consistent across all nodes.
- **HTTPS**: Recommend running behind Nginx with SSL, especially when using Smart Link which passes credentials in URL.

## 🤖 Automation & Workflows

The project includes GitHub Actions workflows for automated maintenance.

### 1. Server Health Check (`health_check.yml`)
- **Schedule**: Runs every 30 minutes.
- **Function**: Checks SSH connectivity of all nodes in `SERVERS_JSON`.
- **Alerts**: Sends notifications to Telegram if nodes are down.
- **Configuration**: Requires `SERVERS_JSON` and `TELEGRAM_JSON` secrets.

### 2. Rotate Cluster Token (`rotate_token.yml`)
- **Trigger**: Manual (`workflow_dispatch`).
- **Function**: Rotates the `--secret` token across all nodes (Slaves first, then Masters).
- **Usage**:
  - Set `SECRET_TOKEN` in Repository Variables.
  - Run workflow to automatically update all nodes via SSH and restart PM2 processes.

### 3. Update Dashboard Password (`update_password.yml`)
- **Trigger**: Manual (`workflow_dispatch`).
- **Function**: Updates the `--auth-password` for the Master Dashboard.
- **Usage**:
  - Set `MASTER_PASSWORD` in Repository Variables.
  - Run workflow to update the Master node's login password.

### Configuration (`SERVERS_JSON`)
JSON array format for server connection details:
```json
[
  {
    "host": "1.2.3.4",
    "port": 22,
    "username": "root",
    "password": "ssh_password",
    "role": "master",
    "path": "/path/to/webssh"
  }
]
```

### Configuration (`TELEGRAM_JSON`)
JSON object for Telegram Bot configuration (used by Health Check):
```json
{
  "token": "123456789:ABCdefGHIjklMNOpqrsTUVwx_yz",
  "chat_id": "987654321"
}
```

## 🗺️ Development Roadmap

This roadmap outlines the next phase of evolution for the WebSSH Cluster, transforming it from a "Server Manager" into a lightweight **"PaaS (Platform as a Service)"** for Python applications.

###  Phase 1: Application Manager (PM2 GUI)
*Goal: Visualize and manage running applications on all nodes.*

- [ ] **Process List**: Extend the Dashboard to show not just system stats, but a list of running PM2 processes on each node.
- [ ] **Process Control**: Add buttons to `Start`, `Stop`, `Restart`, and `Delete` processes directly from the UI.
- [ ] **Logs Viewer**: Ability to stream or fetch `pm2 logs` for a specific process via WebSocket.

###  Phase 2: GitHub One-Click Deploy
*Goal: Deploy a repo from GitHub to a specific node with zero terminal interaction.*

- [ ] **Deployment Wizard**:
    - Input: GitHub Repo URL.
    - Input: Branch (default: main).
    - Input: Project Name (App name).
    - Input: Run Command (e.g., `python app.py`).
- [ ] **Backend Automation** (via SSH):
    1.  **Clone**: `git clone <repo>` to `~/domains/<project>`.
    2.  **Install**: Auto-detect `requirements.txt` (pip) or `package.json` (npm) and install dependencies.
    3.  **Run**: Register with PM2: `pm2 start <cmd> --name <project>`.
- [ ] **Auto-Update**: "Pull & Restart" button to update code from the latest commit.

###  Phase 3: Smart Port Assignment
*Goal: Automatically assign available ports to new applications.*

> **Context**: Ports and Domains are pre-configured/reserved (e.g., via `setup_proxy`). The goal is to find an *unused* one for the new deployment.

- [ ] **Port Discovery**:
    - Fetch reserved ports via `devil port list`.
- [ ] **Idle Detection**:
    - logic: Check which of remaining reserved ports is **NOT** currently listening (using `sockstat` or `lsof` logic).
    - Find the first "Idle" port.
- [ ] **Assignment**:
    - Pass this idle port as an environment variable (e.g., `PORT=12345`) to the new PM2 process.

###  Phase 4: CI/CD Integration
*Goal: Automate deployments via GitHub Actions.*

- [ ] **Webhook Endpoint**: Create a Master API endpoint to receive GitHub Webhooks.


## 🌐 Acknowledgements

Special thanks to the following open-source projects that make this possible:

- **[Tornado](https://www.tornadoweb.org/)**: A scalable, non-blocking web server and web framework.
- **[Paramiko](https://www.paramiko.org/)**: A native Python SSHv2 protocol implementation.
- **[psutil](https://github.com/giampaolo/psutil)**: Cross-platform process and system monitoring.
- **[huashengdun/webssh](https://github.com/huashengdun/webssh)**: The original WebSSH project foundation.

## 📄 License

This project is open-sourced software licensed under the [MIT license](LICENSE).
