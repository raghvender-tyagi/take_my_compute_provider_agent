import os
import time
import json
import platform
import logging
import requests
import psutil
import websocket
import threading
from datetime import datetime
from docker_runner import DockerRunner

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration settings
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000/api/providers/heartbeat/")
PROVIDER_ID = os.getenv("PROVIDER_ID", "provider-default-id")
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "10"))  # in seconds
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN", "")

# Initialize Docker Runner
runner = DockerRunner()
ws = None

def get_system_stats():
    """Gathers system resources stats."""
    try:
        stats = {
            "provider_id": PROVIDER_ID,
            "timestamp": time.time(),
            "cpu_count": psutil.cpu_count(),
            "cpu_usage_percent": psutil.cpu_percent(interval=1),
            "memory_total_gb": round(psutil.virtual_memory().total / (1024 ** 3), 2),
            "memory_used_gb": round(psutil.virtual_memory().used / (1024 ** 3), 2),
            "memory_usage_percent": psutil.virtual_memory().percent,
            "disk_total_gb": round(psutil.disk_usage('/').total / (1024 ** 3), 2),
            "disk_used_gb": round(psutil.disk_usage('/').used / (1024 ** 3), 2),
            "disk_usage_percent": psutil.disk_usage('/').percent,
            "os_name": platform.system(),
            "os_version": platform.release(),
        }
        return stats
    except Exception as e:
        logger.error(f"Error gathering system stats: {e}")
        return None

def send_heartbeat(stats):
    """Sends the gathered statistics to the backend server."""
    try:
        headers = {'Content-Type': 'application/json'}
        if PROVIDER_TOKEN:
            headers['Authorization'] = f'Bearer {PROVIDER_TOKEN}'
        response = requests.post(BACKEND_URL, data=json.dumps(stats), headers=headers, timeout=5)
        if response.status_code in [200, 201]:
            logger.info("Heartbeat sent successfully.")
        else:
            logger.warning(f"Failed to send heartbeat. Server returned status: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error communicating with backend: {e}")

def on_status_change(session_id, status, container_id=None, started_at=None, ended_at=None, error_reason=None):
    """Callback when a Docker container status changes, reporting to the backend."""
    global ws
    payload = {
        "action": "status_update",
        "session_id": session_id,
        "status": status
    }
    if container_id:
        payload["container_id"] = container_id
    if started_at:
        payload["started_at"] = started_at
    if ended_at:
        payload["ended_at"] = ended_at
    if error_reason:
        payload["error_reason"] = error_reason

    sent_via_ws = False
    if ws and ws.sock and ws.sock.connected:
        try:
            ws.send(json.dumps(payload))
            sent_via_ws = True
            logger.info(f"WS: Sent status update for session {session_id} ({status})")
        except Exception as e:
            logger.warning(f"Failed to send status update via WS: {e}")

    if not sent_via_ws:
        # Fallback to HTTP REST API
        try:
            base_api_url = BACKEND_URL.split("providers/heartbeat/")[0]
            url = f"{base_api_url}rentals/{session_id}/agent-update/"
            
            headers = {'Content-Type': 'application/json'}
            if PROVIDER_TOKEN:
                headers['Authorization'] = f'Bearer {PROVIDER_TOKEN}'
                
            response = requests.patch(url, data=json.dumps(payload), headers=headers, timeout=5)
            logger.info(f"HTTP Fallback: Updated session {session_id} to status '{status}': {response.status_code}")
        except Exception as e:
            logger.error(f"Failed to update session status on backend: {e}")

def on_log_line(session_id, log_line):
    """Callback when a new line of log is streamed from the docker sandbox."""
    global ws
    logger.info(f"[Session {session_id} LOG]: {log_line.strip()}")
    if ws and ws.sock and ws.sock.connected:
        try:
            ws.send(json.dumps({
                "action": "log_line",
                "session_id": session_id,
                "log_line": log_line
            }))
        except Exception as e:
            logger.debug(f"Failed to stream log line over WS: {e}")

def check_for_rental_tasks():
    """Fallback REST Polling: checks for assigned, pending, or stopping tasks."""
    try:
        base_api_url = BACKEND_URL.split("providers/heartbeat/")[0]
        url = f"{base_api_url}rentals/"
        
        headers = {}
        if PROVIDER_TOKEN:
            headers['Authorization'] = f'Bearer {PROVIDER_TOKEN}'
            
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            sessions = response.json()
            for session in sessions:
                if session.get("provider_machine_id") == PROVIDER_ID:
                    session_id = session["id"]
                    status = session["status"]
                    
                    if status == "pending":
                        logger.info(f"REST Polling: Found PENDING rental {session_id}. Launching...")
                        runner.run_session(
                            session_id=session_id,
                            image=session["docker_image"],
                            command=session.get("command"),
                            cpu_limit=session["cpu_limit"],
                            memory_limit_mb=session['memory_limit_mb'],
                            status_callback=on_status_change,
                            log_callback=on_log_line
                        )
                    elif status == "stopping":
                        logger.info(f"REST Polling: Found STOPPING request for rental {session_id}. Terminating...")
                        runner.stop_session(session_id)
                        ended_at = datetime.utcnow().isoformat() + "Z"
                        on_status_change(session_id, "stopped", ended_at=ended_at)
    except Exception as e:
        logger.error(f"Error checking for rental tasks: {e}")

def connect_websocket():
    """Initializes and maintains a persistent WebSocket connection to the backend."""
    global ws
    
    # Construct WS URL from HTTP URL
    ws_base = BACKEND_URL.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = ws_base.split("api/providers/heartbeat/")[0] + "ws/agent/"
    
    logger.info(f"Connecting to backend WebSocket at {ws_url}...")

    def on_message(ws_conn, message):
        try:
            data = json.loads(message)
            action = data.get("action")
            if action == "run_task":
                session_id = data["session_id"]
                logger.info(f"WS: Assigned task for session {session_id}")
                runner.run_session(
                    session_id=session_id,
                    image=data["docker_image"],
                    command=data.get("command"),
                    cpu_limit=data["cpu_limit"],
                    memory_limit_mb=data["memory_limit_mb"],
                    status_callback=on_status_change,
                    log_callback=on_log_line
                )
            elif action == "stop_task":
                session_id = data["session_id"]
                logger.info(f"WS: Stop command received for session {session_id}")
                runner.stop_session(session_id)
                ended_at = datetime.utcnow().isoformat() + "Z"
                on_status_change(session_id, "stopped", ended_at=ended_at)
        except Exception as e:
            logger.error(f"WS: Error processing command: {e}")

    def on_error(ws_conn, error):
        logger.error(f"WebSocket error: {error}")

    def on_close(ws_conn, close_status_code, close_msg):
        logger.info("WebSocket connection closed. Reconnecting in 5 seconds...")
        time.sleep(5)
        # Spin up a new thread for reconnection
        threading.Thread(target=connect_websocket, daemon=True).start()

    def on_open(ws_conn):
        logger.info("WebSocket connection established. Registering as provider...")
        ws_conn.send(json.dumps({
            "action": "register",
            "provider_id": PROVIDER_ID
        }))

    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    
    ws.run_forever()

def main():
    logger.info(f"Starting provider monitoring agent: {PROVIDER_ID}")
    
    # 1. Start WebSocket connection in a background thread
    threading.Thread(target=connect_websocket, daemon=True).start()
    
    # 2. Perform initial REST polling check
    check_for_rental_tasks()
    
    last_heartbeat_time = 0
    
    while True:
        current_time = time.time()
        
        # Send heartbeat stats every 10 seconds
        if current_time - last_heartbeat_time >= HEARTBEAT_INTERVAL:
            stats = get_system_stats()
            if stats:
                send_heartbeat(stats)
            last_heartbeat_time = current_time
            
        # REST Polling fallback check every 15 seconds
        if current_time % 15 < 3:
            check_for_rental_tasks()
            
        time.sleep(3)

if __name__ == "__main__":
    main()
