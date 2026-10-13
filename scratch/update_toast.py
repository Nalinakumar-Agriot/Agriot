import os

file_path = "templates/farmer/manage_users.html"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

toast_script = '''
<!-- SweetAlert2 for Toast Messages -->
<script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
<script>
    document.addEventListener('DOMContentLoaded', function() {
        const Toast = Swal.mixin({
          toast: true,
          position: 'top-end',
          showConfirmButton: false,
          timer: 3000,
          timerProgressBar: true,
          didOpen: (toast) => {
            toast.addEventListener('mouseenter', Swal.stopTimer)
            toast.addEventListener('mouseleave', Swal.resumeTimer)
          }
        });

        {% if messages %}
            {% for message in messages %}
                Toast.fire({
                  icon: '{{ message.tags }}' === 'error' ? 'error' : 'success',
                  title: '{{ message }}'
                });
            {% endfor %}
        {% endif %}
    });
</script>
<!-- Append JS -->'''

content = content.replace('<!-- Append JS -->', toast_script)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Added SweetAlert2 Toast messages.")
