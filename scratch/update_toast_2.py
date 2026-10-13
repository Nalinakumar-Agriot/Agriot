import os

file_path = "templates/farmer/manage_users.html"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace SweetAlert2 with Toastify
old_script = '''<!-- SweetAlert2 for Toast Messages -->
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
</script>'''

new_script = '''<!-- Toastify for Toast Messages -->
<link rel="stylesheet" type="text/css" href="https://cdn.jsdelivr.net/npm/toastify-js/src/toastify.min.css">
<script type="text/javascript" src="https://cdn.jsdelivr.net/npm/toastify-js"></script>
<script>
    document.addEventListener('DOMContentLoaded', function() {
        {% if messages %}
            {% for message in messages %}
                Toastify({
                    text: "{{ message }}",
                    duration: 3000,
                    close: true,
                    gravity: "top", // `top` or `bottom`
                    position: "right", // `left`, `center` or `right`
                    stopOnFocus: true,
                    style: {
                        background: '{{ message.tags }}' === 'error' ? 'linear-gradient(to right, #ff5f6d, #ffc371)' : 'linear-gradient(to right, #00b09b, #96c93d)',
                        borderRadius: '8px',
                        boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                        padding: '12px 24px',
                        fontSize: '14px',
                        fontWeight: '500',
                        marginTop: '60px' // Offset for the navbar
                    }
                }).showToast();
            {% endfor %}
        {% endif %}
    });
</script>'''

content = content.replace(old_script, new_script)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Replaced SweetAlert2 with Toastify")
