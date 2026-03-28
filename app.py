from flask import Flask, render_template, request, jsonify, redirect, url_for, Response
from scheduler import Scheduler, Task
from datetime import datetime, timezone
import json
import os
import requests
import warnings
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from satellite_tracking import SatelliteTrackingService

# Suppress insecure request warnings for local camera
warnings.simplefilter('ignore', InsecureRequestWarning)

app = Flask(__name__)
scheduler = Scheduler()
satellite_service = SatelliteTrackingService()

DEFAULT_SATELLITE_POINT_INTERVAL_SECONDS = 30


def _float_env(name, default):
    value = os.getenv(name)
    if value is None or str(value).strip() == '':
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


DEFAULT_OBSERVER = {
    # Override these with env vars if needed:
    # OBSERVER_DEFAULT_LATITUDE, OBSERVER_DEFAULT_LONGITUDE, OBSERVER_DEFAULT_ELEVATION_M
    'latitude': 42.316236,
    'longitude': -71.334760,
    'elevation_m': 70,
}

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


def parse_iso_to_utc(time_str):
    if not time_str:
        raise ValueError('Missing datetime value')
    normalized = time_str.strip()
    if normalized.endswith('Z'):
        normalized = normalized[:-1] + '+00:00'
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_satellite_task_payload(payload):
    satellite_config = payload.get('satellite_config')
    if not isinstance(satellite_config, dict):
        raise ValueError('Satellite tasks require satellite_config')

    norad_id = satellite_config.get('norad_id')
    pass_start = satellite_config.get('pass_start')
    pass_end = satellite_config.get('pass_end')
    satellite_name = satellite_config.get('satellite_name')

    if not norad_id:
        raise ValueError('satellite_config.norad_id is required')
    if not pass_start or not pass_end:
        raise ValueError('satellite_config.pass_start and satellite_config.pass_end are required')

    observer = satellite_config.get('observer') or {}
    latitude = observer.get('latitude', DEFAULT_OBSERVER['latitude'])
    longitude = observer.get('longitude', DEFAULT_OBSERVER['longitude'])
    elevation_m = observer.get('elevation_m', DEFAULT_OBSERVER['elevation_m'])

    try:
        latitude = float(latitude)
        longitude = float(longitude)
        elevation_m = float(elevation_m)
    except (TypeError, ValueError):
        raise ValueError('Observer latitude/longitude/elevation must be numeric')

    if latitude < -90 or latitude > 90:
        raise ValueError('Observer latitude must be between -90 and 90')
    if longitude < -180 or longitude > 180:
        raise ValueError('Observer longitude must be between -180 and 180')

    interval = satellite_config.get('point_interval_seconds', DEFAULT_SATELLITE_POINT_INTERVAL_SECONDS)
    try:
        interval = int(interval)
    except (TypeError, ValueError):
        raise ValueError('point_interval_seconds must be an integer')

    if interval <= 0:
        raise ValueError('point_interval_seconds must be greater than 0')

    track_points = satellite_service.generate_track_points(
        norad_id=norad_id,
        observer_lat=latitude,
        observer_lon=longitude,
        observer_elevation_m=elevation_m,
        pass_start=pass_start,
        pass_end=pass_end,
        point_interval_seconds=interval,
    )

    start_dt = parse_iso_to_utc(pass_start)

    normalized_config = {
        'norad_id': int(norad_id),
        'satellite_name': satellite_name,
        'pass_start': parse_iso_to_utc(pass_start).isoformat(),
        'pass_end': parse_iso_to_utc(pass_end).isoformat(),
        'point_interval_seconds': interval,
        'observer': {
            'latitude': latitude,
            'longitude': longitude,
            'elevation_m': elevation_m,
        },
    }

    return {
        'start_time': start_dt.isoformat(),
        'track_data': track_points,
        'metadata': {'satellite': normalized_config},
    }

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


@app.route('/api/satellites', methods=['GET'])
def list_satellites():
    """List satellites from CelesTrak cache (optionally filtered)."""
    try:
        query = request.args.get('query', '')
        limit = int(request.args.get('limit', 200))
        satellites = satellite_service.list_satellites(query=query, limit=limit)
        return jsonify({'satellites': satellites})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/satellites/passes', methods=['POST'])
def get_satellite_passes():
    """Return upcoming visible passes for a selected satellite and observer location."""
    try:
        data = request.get_json() or {}
        norad_id = data.get('norad_id')
        start_time = data.get('start_time')
        observer = data.get('observer') or {}

        if not norad_id:
            return jsonify({'error': 'norad_id is required'}), 400
        if not start_time:
            return jsonify({'error': 'start_time is required'}), 400

        latitude = float(observer.get('latitude', DEFAULT_OBSERVER['latitude']))
        longitude = float(observer.get('longitude', DEFAULT_OBSERVER['longitude']))
        elevation_m = float(observer.get('elevation_m', DEFAULT_OBSERVER['elevation_m']))
        window_hours = float(data.get('window_hours', 24))
        min_elevation_degrees = float(data.get('min_elevation_degrees', 5))
        max_passes = int(data.get('max_passes', 12))

        passes = satellite_service.get_next_passes(
            norad_id=norad_id,
            observer_lat=latitude,
            observer_lon=longitude,
            observer_elevation_m=elevation_m,
            window_start=start_time,
            window_hours=window_hours,
            min_elevation_degrees=min_elevation_degrees,
            max_passes=max_passes,
        )
        return jsonify({'passes': passes})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/satellites/track', methods=['POST'])
def generate_satellite_track():
    """Generate smooth-style az/el waypoints for a selected satellite pass."""
    try:
        data = request.get_json() or {}
        task_payload = build_satellite_task_payload({'satellite_config': data})
        return jsonify(
            {
                'start_time': task_payload['start_time'],
                'track_data': task_payload['track_data'],
                'metadata': task_payload['metadata'],
            }
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400

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

        # Satellite tasks derive smooth-like track_data from selected pass and observer location
        if data['track_type'] == 'satellite':
            satellite_payload = build_satellite_task_payload(data)
            data['start_time'] = satellite_payload['start_time']
            data['track_data'] = satellite_payload['track_data']
            data['metadata'] = satellite_payload['metadata']
        
        task = Task(
            task_id=data['task_id'],
            name=data['name'],
            track_type=data['track_type'],
            start_time=ensure_utc_time(data['start_time']),
            end_time=ensure_utc_time(data.get('end_time')),
            track_data=data.get('track_data'),
            udp_port=data.get('udp_port'),
            metadata=data.get('metadata')
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
        if 'metadata' in data:
            task.metadata = data.get('metadata') or {}

        if task.track_type == 'satellite' and 'satellite_config' in data:
            satellite_payload = build_satellite_task_payload(data)
            task.start_time = satellite_payload['start_time']
            task.track_data = satellite_payload['track_data']
            task.metadata = satellite_payload['metadata']
        
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
    return render_template(
        'task_form.html',
        task=None,
        readonly=False,
        start_local=None,
        end_local=None,
        observer_defaults=DEFAULT_OBSERVER,
    )

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

        return render_template(
            'task_form.html',
            task=task,
            readonly=(getattr(task, 'status', None) == 'ended'),
            start_local=start_local,
            end_local=end_local,
            observer_defaults=DEFAULT_OBSERVER,
        )
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
