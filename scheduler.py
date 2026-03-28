# Each task object has a unique identifier, a name, and track type (smooth, satellite, or live).
# A smooth track has several nodes with relative timestamps, azimuth and elevation angles, and slew (deg/s).
# A live track represents a start and end absolute time, in which to expect continuous HTTP updates (just az/el values).
# The host server keeps a running JSON list of tasks.

import json
import os
from datetime import datetime

class Task:
    def __init__(self, task_id, name, track_type, start_time, end_time=None, track_data=None, udp_port=None, metadata=None):
        self.task_id = task_id
        self.name = name
        self.track_type = track_type  # 'smooth', 'satellite', or 'live'
        self.track_data = track_data  # List of nodes for smooth tracks only
        self.start_time = start_time  # Required for both smooth and live tasks
        self.end_time = end_time      # Required for live tasks, None for smooth tasks
        self.udp_port = udp_port      # UDP port for live tasks
        self.metadata = metadata or {}  # Extra task-specific metadata (e.g. satellite config)
        self.status = 'pending'       # pending, running, ended
        self.created_at = datetime.now().isoformat()
    
    def to_dict(self):
        """Convert task to dictionary for JSON serialization"""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "track_type": self.track_type,
            "track_data": self.track_data,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": getattr(self, 'status', 'pending'),
            "udp_port": self.udp_port,
            "metadata": self.metadata,
            "created_at": self.created_at
        }
    
    @staticmethod
    def from_dict(data):
        """Create Task object from dictionary"""
        task = Task(
            data["task_id"], 
            data["name"], 
            data["track_type"], 
            data["start_time"],
            data.get("end_time"),
            data.get("track_data"),
            data.get("udp_port"),
            data.get("metadata")
        )
        task.created_at = data.get("created_at", task.created_at)
        task.status = data.get("status", getattr(task, 'status', 'pending'))
        return task
    
    def __repr__(self):
        time_info = f"start={self.start_time}"
        if self.end_time:
            time_info += f", end={self.end_time}"
        return f"Task(id={self.task_id}, name={self.name}, type={self.track_type}, {time_info})"

class Scheduler:
    def __init__(self, tasks_file="tasks.json"):
        self.tasks = []
        self.tasks_file = tasks_file
        self.load_tasks()

    def add_task(self, task):
        self.tasks.append(task)
        self.save_tasks()

    def remove_task(self, task_id):
        original_length = len(self.tasks)
        self.tasks = [task for task in self.tasks if not self._ids_match(task.task_id, task_id)]
        if len(self.tasks) < original_length:
            self.save_tasks()

    def get_tasks(self):
        return self.tasks

    def find_task(self, task_id):
        for task in self.tasks:
            if self._ids_match(task.task_id, task_id):
                return task
        return None
    
    @staticmethod
    def _ids_match(id1, id2):
        """Compare two IDs, handling type conversion for numeric strings and UUIDs"""
        # Direct comparison first
        if id1 == id2:
            return True
        
        # Convert both to strings for comparison
        str_id1 = str(id1).strip()
        str_id2 = str(id2).strip()
        
        if str_id1 == str_id2:
            return True
        
        # Try numeric comparison only if both look like numbers
        try:
            if str_id1.isdigit() and str_id2.isdigit():
                return int(id1) == int(id2)
        except (ValueError, TypeError):
            pass
        
        return False
    
    def save_tasks(self):
        """Save all tasks to JSON file"""
        tasks_data = [task.to_dict() for task in self.tasks]
        with open(self.tasks_file, 'w') as f:
            json.dump(tasks_data, f, indent=2)
    
    def load_tasks(self):
        """Load all tasks from JSON file"""
        if os.path.exists(self.tasks_file):
            try:
                with open(self.tasks_file, 'r') as f:
                    tasks_data = json.load(f)
                    self.tasks = [Task.from_dict(data) for data in tasks_data]
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error loading tasks: {e}")
                self.tasks = []
        else:
            self.tasks = []
