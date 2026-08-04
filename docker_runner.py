import docker
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger("docker_runner")
logger.setLevel(logging.INFO)

class DockerRunner:
    def __init__(self):
        try:
            self.client = docker.from_env()
        except Exception as e:
            logger.error(f"Failed to connect to Docker daemon: {e}")
            self.client = None
            
        self.active_containers = {}  # session_id -> container object
        self.log_threads = {}        # session_id -> thread

    def run_session(self, session_id, image, command, cpu_limit, memory_limit_mb, status_callback, log_callback):
        """Runs a docker container in an isolated sandbox with resource constraints."""
        if not self.client:
            status_callback(session_id, "failed", error_reason="Docker daemon not available on provider host.")
            return

        def run_thread():
            try:
                # 1. Pull the image (Transition to provisioning)
                logger.info(f"Session {session_id}: Pulling image {image}...")
                status_callback(session_id, "provisioning")
                try:
                    self.client.images.pull(image)
                except Exception as pe:
                    logger.error(f"Session {session_id}: Failed to pull image {image}: {pe}")
                    status_callback(session_id, "failed", error_reason=f"Failed to pull image: {str(pe)}")
                    return

                # 2. Configure resource limits
                # 1 CPU core = 1,000,000,000 nano_cpus
                nano_cpus = int(cpu_limit * 1e9)
                # mem_limit format e.g. "512m" for 512 Megabytes
                mem_limit = f"{int(memory_limit_mb)}m"

                logger.info(f"Session {session_id}: Starting container with CPU: {cpu_limit} cores, RAM: {memory_limit_mb}MB")
                
                # Parse command if present
                cmd_arg = command if command and command.strip() else None

                # 3. Spin up the container (Bridge network is default, i.e., isolated from host network)
                container = self.client.containers.run(
                    image,
                    command=cmd_arg,
                    nano_cpus=nano_cpus,
                    mem_limit=mem_limit,
                    network_mode="bridge",
                    detach=True
                )

                self.active_containers[session_id] = container
                started_at = datetime.utcnow().isoformat() + "Z"
                status_callback(session_id, "running", container_id=container.id, started_at=started_at)

                # 4. Start log streaming in a separate daemon thread
                log_thread = threading.Thread(
                    target=self._stream_logs,
                    args=(session_id, container, log_callback),
                    daemon=True
                )
                self.log_threads[session_id] = log_thread
                log_thread.start()

                # 5. Wait for the container to exit
                result = container.wait()
                exit_code = result.get('StatusCode', 0)
                logger.info(f"Session {session_id}: Container exited with code {exit_code}")

                ended_at = datetime.utcnow().isoformat() + "Z"
                
                # Check if it was stopped deliberately
                if session_id not in self.active_containers:
                    # Stopped via stop command
                    status_callback(session_id, "stopped", ended_at=ended_at)
                elif exit_code == 0:
                    status_callback(session_id, "completed", ended_at=ended_at)
                else:
                    status_callback(session_id, "failed", ended_at=ended_at, error_reason=f"Container exited with non-zero code: {exit_code}")

            except Exception as e:
                logger.error(f"Session {session_id}: Error running container: {e}")
                status_callback(session_id, "failed", error_reason=str(e))
            finally:
                self.cleanup_session(session_id)

        threading.Thread(target=run_thread, daemon=True).start()

    def _stream_logs(self, session_id, container, log_callback):
        """Streams container logs and invokes the log callback."""
        try:
            # stream=True returns a generator for stdout/stderr logs
            for log_line in container.logs(stream=True, follow=True):
                if isinstance(log_line, bytes):
                    log_line = log_line.decode('utf-8', errors='ignore')
                log_callback(session_id, log_line)
        except Exception as e:
            logger.debug(f"Log stream ended for session {session_id}: {e}")

    def stop_session(self, session_id):
        """Stops and cleans up the active session container."""
        container = self.active_containers.pop(session_id, None)
        if container:
            try:
                logger.info(f"Stopping container for session {session_id}...")
                container.stop(timeout=5)
                container.remove(force=True)
                logger.info(f"Container removed for session {session_id}")
            except Exception as e:
                logger.error(f"Error stopping container: {e}")
        return True

    def cleanup_session(self, session_id):
        """Removes container and associated references."""
        self.active_containers.pop(session_id, None)
        self.log_threads.pop(session_id, None)
