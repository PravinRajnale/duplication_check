// scripts.js

document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('upload-form');
    const loading = document.getElementById('loading');

    form.addEventListener('submit', function(e) {
        // Simple validation to ensure files are selected
        const files = document.getElementById('formFileMultiple').files;
        if (files.length === 0) {
            e.preventDefault();
            alert('Please select at least one PDF file to upload.');
        } else {
            // Show the loading spinner
            loading.style.display = 'block';
        }
    });
});
