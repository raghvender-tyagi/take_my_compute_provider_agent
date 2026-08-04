import os
import time
import json
import platform
import logging
import threading
import requests
import psutil
import customtkinter as ctk
from datetime import datetime
from docker_runner import DockerRunner
import websocket

# Configure logging
logger = logging.getLogger("gui_agent")
logger.setLevel(logging.INFO)

class TextHandler(logging.Handler):
    """Custom logging handler to redirect logs to CustomTkinter TextBox."""
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record)
        def append():
            self.text_widget.configure(state="normal")
            self.text_widget.insert("end", msg + "\n")
            self.text_widget.configure(state="disabled")
            self.text_widget.yview("end")
        # Ensure thread safety by using Tkinter's after method
        self.text_widget.after(0, append)

class ProviderAgentApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Settings
        self.title("TakeMyCompute - Provider Control Panel")
        self.geometry("850x650")
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        # State Variables
        self.is_sharing = False
        self.agent_thread = None
        self.runner = DockerRunner()
        self.ws = None

        # Load environment defaults
        self.default_url = os.getenv("BACKEND_URL", "https://takemycompute-backend.onrender.com/api/providers/heartbeat/")
        self.default_id = os.getenv("PROVIDER_ID", f"provider-{platform.node().lower()[:8]}")
        self.default_token = os.getenv("PROVIDER_TOKEN", "")

        self.setup_ui()
        self.setup_logging()

    def setup_ui(self):
        # Configure Grid Layout (1 row, 2 columns)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=2)  # Left panel
        self.grid_columnconfigure(1, weight=3)  # Right panel

        # ================= LEFT PANEL (Controls & Settings) =================
        self.left_frame = ctk.CTkFrame(self, corner_radius=12)
        self.left_frame.grid(row=0, column=0, padx=15, pady=15, sticky="nsew")
        self.left_frame.grid_columnconfigure(0, weight=1)

        # Title Label
        self.title_lbl = ctk.CTkLabel(
            self.left_frame, text="TakeMyCompute", 
            font=ctk.CTkFont(size=22, weight="bold")
        )
        self.title_lbl.grid(row=0, column=0, padx=20, pady=20, sticky="w")

        # Subtitle
        self.subtitle_lbl = ctk.CTkLabel(
            self.left_frame, text="Rent out your CPU/RAM securely", 
            font=ctk.CTkFont(size=13, slant="italic"), text_color="gray"
        )
        self.subtitle_lbl.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")

        # Input: Backend URL
        self.url_lbl = ctk.CTkLabel(self.left_frame, text="Backend Server URL", font=ctk.CTkFont(size=12))
        self.url_lbl.grid(row=2, column=0, padx=20, pady=(10, 2), sticky="w")
        self.url_entry = ctk.CTkEntry(self.left_frame, placeholder_text="http://localhost:8000/api/providers/heartbeat/")
        self.url_entry.insert(0, self.default_url)
        self.url_entry.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="ew")

        # Input: Provider ID
        self.id_lbl = ctk.CTkLabel(self.left_frame, text="Unique Provider ID", font=ctk.CTkFont(size=12))
        self.id_lbl.grid(row=4, column=0, padx=20, pady=(10, 2), sticky="w")
        self.id_entry = ctk.CTkEntry(self.left_frame, placeholder_text="e.g. my-desktop-machine")
        self.id_entry.insert(0, self.default_id)
        self.id_entry.grid(row=5, column=0, padx=20, pady=(0, 10), sticky="ew")

        # Input: Login Username
        self.username_lbl = ctk.CTkLabel(self.left_frame, text="Login Username", font=ctk.CTkFont(size=12))
        self.username_lbl.grid(row=6, column=0, padx=20, pady=(10, 2), sticky="w")
        self.username_entry = ctk.CTkEntry(self.left_frame, placeholder_text="Enter username")
        self.username_entry.grid(row=7, column=0, padx=20, pady=(0, 10), sticky="ew")

        # Input: Login Password
        self.password_lbl = ctk.CTkLabel(self.left_frame, text="Login Password", font=ctk.CTkFont(size=12))
        self.password_lbl.grid(row=8, column=0, padx=20, pady=(10, 2), sticky="w")
        self.password_entry = ctk.CTkEntry(self.left_frame, placeholder_text="Enter password", show="*")
        self.password_entry.grid(row=9, column=0, padx=20, pady=(0, 10), sticky="ew")

        # Input: Provider JWT Token
        self.token_lbl = ctk.CTkLabel(self.left_frame, text="Authentication Token (JWT)", font=ctk.CTkFont(size=12))
        self.token_lbl.grid(row=10, column=0, padx=20, pady=(10, 2), sticky="w")
        self.token_entry = ctk.CTkEntry(self.left_frame, placeholder_text="Paste token from dashboard...", show="*")
        self.token_entry.insert(0, self.default_token)
        self.token_entry.grid(row=11, column=0, padx=20, pady=(0, 10), sticky="ew")

        self.fetch_token_btn = ctk.CTkButton(
            self.left_frame, text="Connect",
            fg_color="#4B5563", hover_color="#374151",
            font=ctk.CTkFont(size=12),
            command=self.fetch_token
        )
        self.fetch_token_btn.grid(row=12, column=0, padx=20, pady=(0, 20), sticky="ew")

        # Input: CPU Limit (Cores)
        import psutil
        physical_cpu = psutil.cpu_count(logical=False) or 1
        total_ram = round(psutil.virtual_memory().total / (1024 ** 3), 1)

        self.cpu_limit_lbl = ctk.CTkLabel(self.left_frame, text=f"Max CPU Cores (Host Max: {physical_cpu})", font=ctk.CTkFont(size=12))
        self.cpu_limit_lbl.grid(row=13, column=0, padx=20, pady=(10, 2), sticky="w")
        self.cpu_limit_entry = ctk.CTkEntry(self.left_frame, placeholder_text=str(physical_cpu))
        self.cpu_limit_entry.insert(0, str(physical_cpu))
        self.cpu_limit_entry.grid(row=14, column=0, padx=20, pady=(0, 10), sticky="ew")

        # Input: RAM Limit (GB)
        self.ram_limit_lbl = ctk.CTkLabel(self.left_frame, text=f"Max RAM GB (Host Max: {total_ram})", font=ctk.CTkFont(size=12))
        self.ram_limit_lbl.grid(row=15, column=0, padx=20, pady=(10, 2), sticky="w")
        self.ram_limit_entry = ctk.CTkEntry(self.left_frame, placeholder_text=str(total_ram))
        self.ram_limit_entry.insert(0, str(total_ram))
        self.ram_limit_entry.grid(row=16, column=0, padx=20, pady=(0, 20), sticky="ew")

        # Toggle Button
        self.action_btn = ctk.CTkButton(
            self.left_frame, text="Start Sharing", 
            fg_color="#1f85de", hover_color="#1867ab",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.toggle_sharing
        )
        self.action_btn.grid(row=13, column=0, padx=20, pady=10, sticky="ew")

        # Status Indicator
        self.status_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        self.status_frame.grid(row=14, column=0, padx=20, pady=10, sticky="ew")
        
        self.status_lbl = ctk.CTkLabel(self.status_frame, text="Status: ", font=ctk.CTkFont(size=13))
        self.status_lbl.pack(side="left")
        self.status_val = ctk.CTkLabel(
            self.status_frame, text="INACTIVE", 
            text_color="#e63946", font=ctk.CTkFont(size=13, weight="bold")
        )
        self.status_val.pack(side="left")

        # ================= RIGHT PANEL (Monitoring & Logs) =================
        self.right_frame = ctk.CTkFrame(self, corner_radius=12)
        self.right_frame.grid(row=0, column=1, padx=15, pady=15, sticky="nsew")
        self.right_frame.grid_columnconfigure(0, weight=1)
        self.right_frame.grid_rowconfigure(2, weight=1)  # Allow log box to expand

        # CPU Usage Monitor
        self.cpu_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.cpu_frame.grid(row=0, column=0, padx=20, pady=15, sticky="ew")
        self.cpu_lbl = ctk.CTkLabel(self.cpu_frame, text="CPU Usage: 0%", font=ctk.CTkFont(size=13, weight="bold"))
        self.cpu_lbl.pack(anchor="w")
        self.cpu_progress = ctk.CTkProgressBar(self.cpu_frame)
        self.cpu_progress.set(0)
        self.cpu_progress.pack(fill="x", pady=5)

        # RAM Usage Monitor
        self.ram_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.ram_frame.grid(row=1, column=0, padx=20, pady=(0, 15), sticky="ew")
        self.ram_lbl = ctk.CTkLabel(self.ram_frame, text="RAM Usage: 0% (0.0GB / 0.0GB)", font=ctk.CTkFont(size=13, weight="bold"))
        self.ram_lbl.pack(anchor="w")
        self.ram_progress = ctk.CTkProgressBar(self.ram_frame)
        self.ram_progress.set(0)
        self.ram_progress.pack(fill="x", pady=5)

        # Scrollable Logs Console
        self.log_lbl = ctk.CTkLabel(self.right_frame, text="Agent logs:", font=ctk.CTkFont(size=12))
        self.log_lbl.grid(row=2, column=0, padx=20, pady=(5, 0), sticky="w")
        
        self.log_box = ctk.CTkTextbox(self.right_frame, font=ctk.CTkFont(family="Consolas", size=11))
        self.log_box.grid(row=3, column=0, padx=20, pady=10, sticky="nsew")
        self.log_box.configure(state="disabled")

    def setup_logging(self):
        # Configure logging format
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
        
        # Link our custom handler to the textbox
        text_handler = TextHandler(self.log_box)
        text_handler.setFormatter(formatter)
        
        # Also print to python standard output
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        logger.addHandler(text_handler)
        logger.addHandler(console_handler)
        logger.info("Control Panel initialized. Ready to share.")

    def fetch_token(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        backend_url = self.url_entry.get().strip()

        if not username or not password:
            logger.warning("Please enter both username and password to connect.")
            return

        base_api_url = backend_url
        if "api/providers/heartbeat" in backend_url:
            base_api_url = backend_url.split("api/providers/heartbeat")[0]

        login_url = base_api_url.rstrip("/") + "/api/auth/api/login/"
        payload = {"username": username, "password": password}
        headers = {"Content-Type": "application/json"}

        logger.info(f"Connecting to backend login endpoint: {login_url}")
        try:
            response = requests.post(login_url, json=payload, headers=headers, timeout=8)
            if response.status_code == 200:
                token = response.json().get("access")
                if token:
                    self.token_entry.delete(0, "end")
                    self.token_entry.insert(0, token)
                    logger.info("Connected successfully. Token fetched and stored.")
                else:
                    logger.error("Login succeeded but access token was not found in response.")
            else:
                logger.error(f"Connect failed ({response.status_code}): {response.text}")
        except Exception as e:
            logger.error(f"Failed to connect to backend: {e}")

    def toggle_sharing(self):
        if not self.is_sharing:
            # Start Sharing Action
            self.is_sharing = True
            self.action_btn.configure(text="Stop Sharing", fg_color="#e63946", hover_color="#c92a3a")
            self.status_val.configure(text="SHARING ACTIVE", text_color="#2b9348")
            
            # Disable configuration fields during execution
            self.url_entry.configure(state="disabled")
            self.id_entry.configure(state="disabled")
            self.token_entry.configure(state="disabled")

            # Start background thread for agent statistics gathering
            self.agent_thread = threading.Thread(target=self.run_agent_loop, daemon=True)
            self.agent_thread.start()
            
            # Start background WebSocket connection thread
            backend_url = self.url_entry.get().strip()
            threading.Thread(target=self.connect_websocket, args=(backend_url,), daemon=True).start()
            logger.info("Sharing started. System metrics reporting active.")
        else:
            # Stop Sharing Action
            self.is_sharing = False
            if self.ws:
                try:
                    self.ws.close()
                except Exception:
                    pass
            self.action_btn.configure(text="Start Sharing", fg_color="#1f85de", hover_color="#1867ab")
            self.status_val.configure(text="INACTIVE", text_color="#e63946")
            
            # Re-enable inputs
            self.url_entry.configure(state="normal")
            self.id_entry.configure(state="normal")
            self.token_entry.configure(state="normal")
            logger.info("Sharing stopped. Agent set to standby mode.")

    def get_system_stats(self):
        """Gathers system resources stats."""
        try:
            # Read user-defined limits
            try:
                allowed_cpu = int(self.cpu_limit_entry.get().strip())
            except ValueError:
                allowed_cpu = psutil.cpu_count()
                
            try:
                allowed_ram_gb = float(self.ram_limit_entry.get().strip())
            except ValueError:
                allowed_ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 2)

            stats = {
                "provider_id": self.id_entry.get().strip(),
                "timestamp": time.time(),
                "cpu_count": allowed_cpu,
                "cpu_usage_percent": psutil.cpu_percent(interval=None), # non-blocking
                "memory_total_gb": allowed_ram_gb,
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

    def run_agent_loop(self):
        backend_url = self.url_entry.get().strip()
        token = self.token_entry.get().strip()

        # Update stats loop
        while self.is_sharing:
            stats = self.get_system_stats()
            if stats:
                # Update UI Progress Bars & Labels in thread-safe manner
                cpu_percent = stats['cpu_usage_percent']
                ram_percent = stats['memory_usage_percent']
                ram_used = stats['memory_used_gb']
                ram_total = stats['memory_total_gb']
                
                self.cpu_lbl.configure(text=f"CPU Usage: {cpu_percent}%")
                self.cpu_progress.set(cpu_percent / 100.0)
                
                self.ram_lbl.configure(text=f"RAM Usage: {ram_percent}% ({ram_used}GB / {ram_total}GB)")
                self.ram_progress.set(ram_percent / 100.0)

                # Send data to Backend API
                try:
                    headers = {'Content-Type': 'application/json'}
                    if token:
                        headers['Authorization'] = f'Bearer {token}'
                        
                    response = requests.post(backend_url, data=json.dumps(stats), headers=headers, timeout=4)
                    
                    if response.status_code in [200, 201]:
                        logger.info(f"Heartbeat OK -> CPU: {cpu_percent}%, RAM: {ram_percent}%")
                    else:
                        logger.warning(f"Heartbeat failed. HTTP Status: {response.status_code}")
                except Exception as e:
                    logger.error(f"Failed to connect to backend: {e}")

            # Initial task check
            self.check_for_rental_tasks(backend_url, token)

            # Sleep for 10 seconds, but check for tasks every 3 seconds, and check if sharing was stopped
            for i in range(20):
                if not self.is_sharing:
                    break
                if i % 6 == 0:  # approximately every 3 seconds (6 * 0.5s)
                    self.check_for_rental_tasks(backend_url, token)
                time.sleep(0.5)

    def on_status_change(self, session_id, status, container_id=None, started_at=None, ended_at=None, error_reason=None):
        backend_url = self.url_entry.get().strip()
        token = self.token_entry.get().strip()
        
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

        status_label = status.replace("_", " ").title()
        logger.info(f"Status update: session {session_id} -> {status_label}")

        sent_via_ws = False
        if self.ws and self.ws.sock and self.ws.sock.connected:
            try:
                self.ws.send(json.dumps(payload))
                sent_via_ws = True
                logger.info(f"WS: Sent status update for session {session_id} ({status})")
            except Exception as e:
                logger.warning(f"Failed to send status update via WS: {e}")

        if not sent_via_ws:
            try:
                base_api_url = backend_url.split("providers/heartbeat/")[0]
                url = f"{base_api_url}rentals/{session_id}/agent-update/"
                
                headers = {'Content-Type': 'application/json'}
                if token:
                    headers['Authorization'] = f'Bearer {token}'
                    
                response = requests.patch(url, data=json.dumps(payload), headers=headers, timeout=5)
                logger.info(f"HTTP Fallback: Updated session {session_id} to status '{status}': {response.status_code}")
            except Exception as e:
                logger.error(f"Failed to update session status on backend: {e}")

        if status == "completed":
            logger.info(f"Task completed successfully for session {session_id}.")
        elif status == "running":
            logger.info(f"Task is now running for session {session_id}.")
        elif status == "stopped":
            logger.info(f"Task stopped for session {session_id}.")
        else:
            if status == "completed":
                logger.info(f"Task completed for session {session_id}.")

    def on_log_line(self, session_id, log_line):
        logger.info(f"[Session {session_id} LOG]: {log_line.strip()}")
        if self.ws and self.ws.sock and self.ws.sock.connected:
            try:
                self.ws.send(json.dumps({
                    "action": "log_line",
                    "session_id": session_id,
                    "log_line": log_line
                }))
            except Exception as e:
                logger.debug(f"Failed to stream log line over WS: {e}")

    def check_for_rental_tasks(self, backend_url, token):
        provider_id = self.id_entry.get().strip()
        try:
            base_api_url = backend_url.split("providers/heartbeat/")[0]
            url = f"{base_api_url}rentals/"
            
            headers = {}
            if token:
                headers['Authorization'] = f'Bearer {token}'
                
            response = requests.get(url, headers=headers, timeout=4)
            if response.status_code == 200:
                sessions = response.json()
                for session in sessions:
                    if session.get("provider_machine_id") == provider_id:
                        session_id = session["id"]
                        status = session["status"]
                        
                        if status == "pending":
                            logger.info(f"REST Polling Fallback: Found PENDING rental session {session_id}. Launching...")
                            self.runner.run_session(
                                session_id=session_id,
                                image=session["docker_image"],
                                command=session.get("command"),
                                cpu_limit=session["cpu_limit"],
                                memory_limit_mb=session['memory_limit_mb'],
                                status_callback=self.on_status_change,
                                log_callback=self.on_log_line
                            )
                        elif status == "stopping":
                            logger.info(f"REST Polling Fallback: Found STOPPING request for rental session {session_id}. Terminating...")
                            self.runner.stop_session(session_id)
                            ended_at = datetime.utcnow().isoformat() + "Z"
                            self.on_status_change(session_id, "stopped", ended_at=ended_at)
        except Exception as e:
            logger.error(f"Error checking for rental tasks in GUI: {e}")

    def connect_websocket(self, backend_url):
        ws_base = backend_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = ws_base.split("api/providers/heartbeat/")[0] + "ws/agent/"
        provider_id = self.id_entry.get().strip()

        logger.info(f"Connecting to backend WebSocket at {ws_url}...")

        def on_message(ws_conn, message):
            try:
                data = json.loads(message)
                action = data.get("action")
                if action == "run_task":
                    session_id = data["session_id"]
                    logger.info(f"Task received: starting session {session_id}.")
                    self.runner.run_session(
                        session_id=session_id,
                        image=data["docker_image"],
                        command=data.get("command"),
                        cpu_limit=data["cpu_limit"],
                        memory_limit_mb=data["memory_limit_mb"],
                        status_callback=self.on_status_change,
                        log_callback=self.on_log_line
                    )
                elif action == "stop_task":
                    session_id = data["session_id"]
                    logger.info(f"Task received: stop command for session {session_id}.")
                    self.runner.stop_session(session_id)
                    ended_at = datetime.utcnow().isoformat() + "Z"
                    self.on_status_change(session_id, "stopped", ended_at=ended_at)
            except Exception as e:
                logger.error(f"WS: Error processing command: {e}")

        def on_error(ws_conn, error):
            logger.error(f"WebSocket error: {error}")

        def on_close(ws_conn, close_status_code, close_msg):
            logger.info("WebSocket connection closed.")
            if self.is_sharing:
                logger.info("Reconnecting in 5 seconds...")
                time.sleep(5)
                if self.is_sharing:
                    threading.Thread(target=self.connect_websocket, args=(backend_url,), daemon=True).start()

        def on_open(ws_conn):
            logger.info("WebSocket connection established successfully.")
            logger.info("Registering provider with the backend...")
            ws_conn.send(json.dumps({
                "action": "register",
                "provider_id": provider_id
            }))

        def on_pong(ws_conn, data):
            logger.info("WebSocket heartbeat pong received.")

        self.ws = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_pong=on_pong
        )
        self.ws.run_forever()

if __name__ == "__main__":
    app = ProviderAgentApp()
    app.mainloop()
