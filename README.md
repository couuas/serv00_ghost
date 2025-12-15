# WebSSH Cluster Manager

![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/couuas/webssh/deploy.yml?label=Deploy)
![Python](https://img.shields.io/badge/python-3.7+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

**[🔴 Live Demo](https://cluster.serv00.us.kg)**

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

## 📄 License
MIT License