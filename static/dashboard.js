// Global tasks list
let currentTasks = [];

// Fetch and display tasks on page load
document.addEventListener('DOMContentLoaded', function() {
    loadTasks();
    updateRotatorStatus();
    updateDetailedStatus();
    // Poll every 5 minutes
    setInterval(updateRotatorStatus, 5 * 60 * 1000);
    // Poll detailed status every 2 seconds
    setInterval(updateDetailedStatus, 2000);
    // Refresh camera snapshot every 1 second
    setInterval(updateCameraSnapshot, 1000);
});

function updateCameraSnapshot() {
    const img = document.getElementById('camera-preview');
    if (img) {
        // Add timestamp to force browser to reload image
        const timestamp = new Date().getTime();
        // Keep the base src but update/add the query param
        // Since we know the src is /api/camera/snapshot, we can just rebuild it
        img.src = `/api/camera/snapshot?t=${timestamp}`;
    }
}

function updateRotatorStatus() {
    fetch('/api/rotator/ping')
        .then(response => response.json())
        .then(data => {
            const indicator = document.getElementById('rotator-status');
            const text = indicator.querySelector('.status-text');
            
            // Remove both classes first
            indicator.classList.remove('online', 'offline');
            
            if (data.status === 'online') {
                indicator.classList.add('online');
                text.textContent = 'Online';
            } else {
                indicator.classList.add('offline');
                text.textContent = 'Offline';
            }
        })
        .catch(error => {
            console.error('Error checking rotator status:', error);
            const indicator = document.getElementById('rotator-status');
            const text = indicator.querySelector('.status-text');
            
            indicator.classList.remove('online');
            indicator.classList.add('offline');
            text.textContent = 'Offline';
        });
}

function updateDetailedStatus() {
    fetch('/api/rotator/status')
        .then(response => response.json())
        .then(data => {
            if (data.error) return;
            
            document.getElementById('azimuth-val').textContent = data.azimuth.toFixed(2) + '°';
            document.getElementById('azimuth-speed').textContent = data.az_speed.toFixed(2);
            
            document.getElementById('elevation-val').textContent = data.elevation.toFixed(2) + '°';
            document.getElementById('elevation-speed').textContent = data.el_speed.toFixed(2);
        })
        .catch(console.error);
}

function loadTasks() {
    fetch('/api/tasks')
        .then(response => response.json())
        .then(tasks => {
            currentTasks = tasks;
        })
        .catch(error => console.error('Error loading tasks:', error));
}

function deleteTask(taskId) {
    if (!confirm('Are you sure you want to delete this task?')) {
        return;
    }

    fetch(`/api/tasks/${taskId}`, {
        method: 'DELETE'
    })
    .then(response => {
        if (response.ok) {
            location.reload();
        } else {
            alert('Error deleting task');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Error deleting task');
    });
}
