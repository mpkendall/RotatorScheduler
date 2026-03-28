import threading
import time
import socket
import json
from datetime import datetime, timezone, timedelta
from scheduler import Scheduler, Task
from rotator import Rotator

class TaskExecutor:
    """Executes scheduled tasks by sending rotator commands at appropriate times"""
    
    def __init__(self, scheduler, rotator, check_interval=1):
        """
        Initialize the task executor
        
        Args:
            scheduler: Scheduler instance containing tasks
            rotator: Rotator instance for sending commands
            check_interval: How often to check for tasks to execute (seconds)
        """
        self.scheduler = scheduler
        self.rotator = rotator
        self.check_interval = check_interval
        self.running = False
        self.thread = None
        self.active_task = None
        self.active_task_start_time = None
        self.active_udp_sockets = {}  # Map of task_id to UDP socket
        self.completed_tasks = set()  # Track completed tasks to prevent restart
        self.started_tasks = {}  # Map of task_id to start time for tasks that have been started
        self.started_at = None  # Executor start time to avoid executing past tasks on startup
        
    def start(self):
        """Start the task executor in a background thread"""
        if self.running:
            return
        # record when the executor started so we don't run tasks whose start_time is in the past
        self.started_at = datetime.now(timezone.utc)
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        print("Task executor started")
    
    def stop(self):
        """Stop the task executor"""
        self.running = False
        # Close all active UDP sockets (copy items to avoid mutation during loop)
        for task_id, sock in list(self.active_udp_sockets.items()):
            try:
                sock.close()
                print(f"Closed UDP socket for task {task_id}")
            except Exception as e:
                print(f"Error closing socket in stop() for {task_id}: {e}")
        self.active_udp_sockets.clear()
        self.completed_tasks.clear()
        self.started_tasks.clear()
        if self.thread:
            self.thread.join(timeout=5)
        print("Task executor stopped")
    
    def _run(self):
        """Main loop - check for tasks to execute"""
        while self.running:
            try:
                self._check_and_execute_tasks()
            except Exception as e:
                print(f"Error in task executor: {e}")
            
            time.sleep(self.check_interval)
    
    def _check_and_execute_tasks(self):
        """Check if any tasks need to be executed"""
        now_utc = datetime.now(timezone.utc)
        tasks = self.scheduler.get_tasks()
        
        # Remove completed/started tasks that are no longer in the scheduler
        self.completed_tasks = {task_id for task_id in self.completed_tasks if any(t.task_id == task_id for t in tasks)}
        self.started_tasks = {task_id: start_time for task_id, start_time in self.started_tasks.items() if any(t.task_id == task_id for t in tasks)}

        
        
        for task in tasks:
            # Skip if this task has already been completed
            if task.task_id in self.completed_tasks:
                continue
            
            # Parse task start time (assuming ISO format with or without timezone)
            try:
                # Handle ISO format datetime strings
                if task.start_time.endswith('Z'):
                    task_start = datetime.fromisoformat(task.start_time.replace('Z', '+00:00'))
                else:
                    # Try to parse as naive datetime and assume UTC
                    task_start = datetime.fromisoformat(task.start_time)
                    if task_start.tzinfo is None:
                        task_start = task_start.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue
            
            # Check if task start time has been reached and not yet started
            # Do not execute tasks whose start_time is before the executor was started
            if (self.started_at is not None and task_start >= self.started_at
                    and now_utc >= task_start and task.task_id not in self.started_tasks):
                self._execute_task(task, task_start)
                self.started_tasks[task.task_id] = task_start
            
            # Check if task end time has passed (for live tasks) - only if task has been started
            if task.task_id in self.started_tasks and task.track_type == 'live' and task.end_time:
                try:
                    if task.end_time.endswith('Z'):
                        task_end = datetime.fromisoformat(task.end_time.replace('Z', '+00:00'))
                    else:
                        task_end = datetime.fromisoformat(task.end_time)
                        if task_end.tzinfo is None:
                            task_end = task_end.replace(tzinfo=timezone.utc)
                    
                    if now_utc > task_end:
                        # Mark completed and remove started marker to avoid races
                        self.completed_tasks.add(task.task_id)
                        self.started_tasks.pop(task.task_id, None)
                        # Close UDP socket if open (use pop to avoid KeyError from race)
                        sock = self.active_udp_sockets.pop(task.task_id, None)
                        if sock:
                            try:
                                sock.close()
                                print(f"Closed UDP socket for task {task.task_id}")
                            except Exception as e:
                                print(f"Error closing UDP socket for task {task.task_id}: {e}")
                        else:
                            print(f"No active UDP socket to close for task {task.task_id}")
                        # mark task ended and persist
                        try:
                            task.status = 'ended'
                            try:
                                self.scheduler.save_tasks()
                            except Exception:
                                pass
                        except Exception:
                            pass
                        print(f"Task {task.task_id} ended")
                except (ValueError, AttributeError):
                    pass
            
            # Check if smooth track has completed - only if task has been started
            if task.task_id in self.started_tasks and task.track_type in ('smooth', 'satellite'):
                # Smooth tracks are considered done after they execute
                # Check if all waypoints have been processed (look at task end based on last waypoint)
                if task.track_data:
                    try:
                        last_waypoint = task.track_data[-1]
                        last_offset = int(last_waypoint.get('time_offset', 0))
                        task_start = self.started_tasks[task.task_id]
                        task_completion_time = task_start + timedelta(seconds=last_offset)
                        
                        if now_utc > task_completion_time:
                            self.completed_tasks.add(task.task_id)
                            self.started_tasks.pop(task.task_id, None)
                            # mark task ended and persist
                            try:
                                task.status = 'ended'
                                try:
                                    self.scheduler.save_tasks()
                                except Exception:
                                    pass
                            except Exception:
                                pass
                            print(f"{task.track_type.title()} track task {task.task_id} completed")
                    except:
                        pass
    
    def _execute_task(self, task, task_start):
        """Execute a task by sending rotator commands"""
        print(f"Starting task: {task.name} (ID: {task.task_id})")
        # mark task as running and persist state
        try:
            task.status = 'running'
            try:
                self.scheduler.save_tasks()
            except Exception:
                pass
        except Exception:
            pass
        
        if task.track_type in ('smooth', 'satellite'):
            self._execute_smooth_track(task, task_start)
        elif task.track_type == 'live':
            self._execute_live_track(task, task_start)
    
    def _execute_smooth_track(self, task, task_start):
        """Execute a smooth track task with waypoint tracking"""
        if not task.track_data or len(task.track_data) == 0:
            print(f"Task {task.task_id} has no track data")
            return
        
        print(f"Executing smooth track: {task.name}")
        
        # Send initial position immediately
        first_point = task.track_data[0]
        self._send_rotator_command(first_point['azimuth'], first_point['elevation'])
        
        # Schedule subsequent points based on time offsets
        threading.Thread(
            target=self._track_smooth_waypoints,
            args=(task, task_start),
            daemon=True
        ).start()
    
    def _track_smooth_waypoints(self, task, task_start):
        """Track smooth waypoints, sending commands at appropriate times"""
        print(f"Task {task.task_id}: starting waypoint tracking, task start time: {task_start}")
        
        for i, point in enumerate(task.track_data):
            # Stop if task is marked as completed
            if task.task_id in self.completed_tasks or not self.running:
                break
            
            # Calculate when this point should be reached by adding time offset to start time
            time_offset_seconds = int(point['time_offset'])
            point_time = task_start + timedelta(seconds=time_offset_seconds)
            
            # Wait until it's time to send this waypoint
            now_utc = datetime.now(timezone.utc)
            time_to_wait = (point_time - now_utc).total_seconds()
            
            print(f"Task {task.task_id}: waypoint {i+1}/{len(task.track_data)} scheduled for {point_time}, time to wait: {time_to_wait:.2f}s")
            
            if time_to_wait > 0:
                time.sleep(time_to_wait)
            elif time_to_wait < -5:
                # Skip waypoints that are more than 5 seconds in the past
                print(f"Task {task.task_id}: skipping waypoint {i+1} (too far in past, offset by {time_to_wait:.2f}s)")
                continue
            
            if self.running and task.task_id not in self.completed_tasks:
                self._send_rotator_command(point['azimuth'], point['elevation'])
                print(f"Task {task.task_id}: sent waypoint {i+1}/{len(task.track_data)} - Az: {point['azimuth']}°, El: {point['elevation']}°")
    
    def _execute_live_track(self, task, task_start):
        """Execute a live track task by opening a UDP port"""
        if not task.udp_port:
            print(f"Live track {task.task_id} has no UDP port configured")
            return
        
        print(f"Live track started: {task.name} on UDP port {task.udp_port}")
        
        # Start UDP listener in a background thread
        threading.Thread(
            target=self._listen_udp_updates,
            args=(task,),
            daemon=True
        ).start()
    
    def _send_rotator_command(self, azimuth, elevation):
        """Send a command to the rotator"""
        try:
            result = self.rotator.move_to(azimuth, elevation)
            if result:
                print(f"Rotator command sent: Az={azimuth}°, El={elevation}° - Result: {result}")
            else:
                print(f"Rotator command sent: Az={azimuth}°, El={elevation}° - No response")
        except Exception as e:
            print(f"Error sending rotator command: {e}")
    
    def _listen_udp_updates(self, task):
        """Listen for UDP updates with azimuth and elevation values"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(2)  # 2 second timeout to check if task is still active
            sock.bind(('0.0.0.0', task.udp_port))
            
            self.active_udp_sockets[task.task_id] = sock
            print(f"UDP listener opened for task {task.task_id} on port {task.udp_port}")
            
            while self.running and task.task_id not in self.completed_tasks:
                try:
                    data, addr = sock.recvfrom(1024)
                    
                    if not self.running or task.task_id in self.completed_tasks:
                        break
                    
                    try:
                        # Parse JSON message
                        message = json.loads(data.decode('utf-8'))
                        
                        if 'azimuth' in message and 'elevation' in message:
                            az = message['azimuth']
                            el = message['elevation']
                            self._send_rotator_command(az, el)
                            print(f"Live track {task.task_id}: received update from {addr[0]}:{addr[1]} - Az: {az}°, El: {el}°")
                        else:
                            print(f"Live track {task.task_id}: received message from {addr[0]}:{addr[1]} with missing fields: {message}")
                    except json.JSONDecodeError:
                        print(f"Live track {task.task_id}: received invalid JSON from {addr[0]}:{addr[1]}")
                    except Exception as e:
                        print(f"Live track {task.task_id}: error processing UDP message: {e}")
                
                except socket.timeout:
                    # Timeout is normal, just check if task is still active and loop again
                    continue
                except Exception as e:
                    if self.running and task.task_id not in self.completed_tasks:
                        print(f"UDP listener error for task {task.task_id}: {e}")
                    break
            
            sock.close()
            if task.task_id in self.active_udp_sockets:
                del self.active_udp_sockets[task.task_id]
            print(f"UDP listener closed for task {task.task_id}")
        
        except Exception as e:
            print(f"Error opening UDP listener for task {task.task_id} on port {task.udp_port}: {e}")
