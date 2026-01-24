#!/usr/bin/env python
"""
Main entry point for the Rotator Task Scheduler
Runs both the Flask web interface and the background task executor
"""

import threading
from app import app, scheduler
from rotator import Rotator
from task_executor import TaskExecutor

# Create rotator instance
rotator = Rotator("http://192.168.1.43")
app.rotator = rotator

# Create and start task executor
executor = TaskExecutor(scheduler, rotator, check_interval=1)

def run_scheduler():
    """Run the task executor in a separate thread"""
    executor.start()

if __name__ == '__main__':
    # Start task executor in background thread
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=False)
    scheduler_thread.start()
    
    try:
        # Run Flask app
        print("Starting Rotator Task Scheduler")
        print("Web interface: http://localhost:5000")
        app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
    finally:
        # Stop executor when Flask app exits
        executor.stop()
