from flask import Flask, render_template, request, jsonify, redirect, url_for, Response
from scheduler import Scheduler, Task
from datetime import datetime, timezone
import json
import requests
import warnings
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# Suppress insecure request warnings for local camera
warnings.simplefilter('ignore', InsecureRequestWarning)

app = Flask(__name__)
scheduler = Scheduler()

def ensure_utc_time(time_str):
    """Convert datetime string to UTC ISO format"""
    if not time_str:
        return None
    try:
        # Parse the datetime-local input (format: YYYY-MM-DDTHH:mm)
        dt = datetime.fromisoformat(time_str)
        # Assume it's UTC and add timezone info
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Return as ISO format string
        return dt.isoformat()
    except:
        return time_str

@app.route('/')
def dashboard():
    """Main dashboard showing all tasks"""
    tasks = scheduler.get_tasks()
    return render_template('dashboard.html', tasks=tasks)

@app.route('/api/rotator/ping')
def ping_rotator():
    """Check if rotator is online"""
    rotator = getattr(app, 'rotator', None)
    if rotator and rotator.ping():
        return jsonify({'status': 'online'})
    return jsonify({'status': 'offline'})

@app.route('/api/rotator/status')
def get_rotator_status():
    """Get detailed rotator status"""
    rotator = getattr(app, 'rotator', None)
    if rotator:
        status = rotator.get_status()
        if status:
            return jsonify(status)
    return jsonify({'error': 'Could not fetch status'}), 503

@app.route('/api/camera/snapshot')
def get_camera_snapshot():
    """Proxy camera snapshot to avoid CORS and Auth exposure"""
    camera_url = "https://192.168.1.226/cgi-bin/api.cgi?cmd=Snap&channel=0&rs=wuuPhkmUCeI9WG7C&user=admin&password=Maker$pace!18"
    try:
        resp = requests.get(camera_url, verify=False, timeout=5)
        return Response(resp.content, mimetype=resp.headers.get('content-type', 'image/jpeg'))
    except Exception as e:
        # Return a placeholder or error indication image if needed, or just 500
        return jsonify({'error': str(e)}), 500

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    """API endpoint to get all tasks as JSON"""
    tasks = scheduler.get_tasks()
    return jsonify([task.to_dict() for task in tasks])

@app.route('/api/tasks', methods=['POST'])
def create_task():
    """API endpoint to create a new task"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['task_id', 'name', 'track_type', 'start_time']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400
        
        # For live tasks, end_time and udp_port are required
        if data['track_type'] == 'live':
            if 'end_time' not in data or not data['end_time']:
                return jsonify({'error': 'Live tasks require end_time'}), 400
            if 'udp_port' not in data or not data['udp_port']:
                data['udp_port'] = 4321
        
        # For smooth tasks, track_data is required
        if data['track_type'] == 'smooth' and 'track_data' not in data:
            return jsonify({'error': 'Smooth tasks require track_data'}), 400
        
        task = Task(
            task_id=data['task_id'],
            name=data['name'],
            track_type=data['track_type'],
            start_time=ensure_utc_time(data['start_time']),
            end_time=ensure_utc_time(data.get('end_time')),
            track_data=data.get('track_data'),
            udp_port=data.get('udp_port')
        )
        
        scheduler.add_task(task)
        return jsonify(task.to_dict()), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/tasks/<task_id>', methods=['GET'])
def get_task(task_id):
    """API endpoint to get a specific task"""
    task = scheduler.find_task(task_id)
    if task:
        return jsonify(task.to_dict())
    return jsonify({'error': 'Task not found'}), 404

@app.route('/api/tasks/<task_id>', methods=['PUT'])
def update_task(task_id):
    """API endpoint to update a task"""
    try:
        task = scheduler.find_task(task_id)
        if not task:
            return jsonify({'error': 'Task not found'}), 404
        
        data = request.get_json()
        
        # Do not allow editing of tasks that are ended
        if getattr(task, 'status', None) == 'ended':
            return jsonify({'error': 'Cannot edit a task that has ended'}), 400

        # Update fields if provided
        if 'name' in data:
            task.name = data['name']
        if 'track_type' in data:
            task.track_type = data['track_type']
        if 'track_data' in data:
            task.track_data = data['track_data']
        if 'start_time' in data:
            task.start_time = ensure_utc_time(data['start_time'])
        if 'end_time' in data:
            task.end_time = ensure_utc_time(data['end_time'])
        if 'udp_port' in data:
            task.udp_port = data['udp_port']
        
        scheduler.save_tasks()
        return jsonify(task.to_dict())
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    """API endpoint to delete a task"""
    task = scheduler.find_task(task_id)
    if task:
        scheduler.remove_task(task_id)
        return jsonify({'message': 'Task deleted successfully'})
    return jsonify({'error': 'Task not found'}), 404

@app.route('/task/new')
def new_task_form():
    """Form to create a new task"""
    # For a new task no times to pre-fill
    return render_template('task_form.html', task=None, readonly=False, start_local=None, end_local=None)

@app.route('/task/<task_id>/edit')
def edit_task_form(task_id):
    """Form to edit a task"""
    task = scheduler.find_task(task_id)
    if task:
        # Prepare local-formatted datetimes for datetime-local inputs (YYYY-MM-DDTHH:MM)
        def iso_to_local(dt_str):
            if not dt_str:
                return None
            try:
                # Parse ISO format (with or without timezone)
                if dt_str.endswith('Z'):
                    dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                else:
                    dt = datetime.fromisoformat(dt_str)
                # Convert to UTC and format without seconds and timezone for datetime-local
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_utc = dt.astimezone(timezone.utc)
                return dt_utc.strftime('%Y-%m-%dT%H:%M')
            except Exception:
                return None

        start_local = iso_to_local(getattr(task, 'start_time', None))
        end_local = iso_to_local(getattr(task, 'end_time', None))

        return render_template('task_form.html', task=task, readonly=(getattr(task, 'status', None) == 'ended'), start_local=start_local, end_local=end_local)
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
