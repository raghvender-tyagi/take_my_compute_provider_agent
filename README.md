# TakeMyCompute Provider Agent

This repository contains the standalone provider agent used to connect a machine to the TakeMyCompute backend.

## Files
- `agent.py` - main agent loop and heartbeat/websocket logic
- `docker_runner.py` - Docker container lifecycle management
- `gui_agent.py` - optional desktop control panel

## Setup
1. Install Python dependencies:
   `pip install -r requirements.txt`
2. Configure environment variables:
   - `BACKEND_URL`
   - `PROVIDER_ID`
   - `PROVIDER_TOKEN`
3. Run:
   `python agent.py`
