import os

file_path = "templates/farmer/manage_users.html"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Stats
content = content.replace('<div class="fs-4 fw-bold">4</div>', '<div class="fs-4 fw-bold">{{ total_staff }}</div>')
content = content.replace('<div class="fs-4 fw-bold">3</div>', '<div class="fs-4 fw-bold">{{ active_staff }}</div>')
# Since suspended and admins are both '1', replace carefully using surrounding HTML
content = content.replace('''          <span class="text-uppercase small fw-bold text-danger" style="font-size:10px; letter-spacing:1px;">
            Suspended
          </span>
          <div class="fs-4 fw-bold">1</div>''', '''          <span class="text-uppercase small fw-bold text-danger" style="font-size:10px; letter-spacing:1px;">
            Suspended
          </span>
          <div class="fs-4 fw-bold">{{ suspended_staff }}</div>''')

content = content.replace('''          <span class="text-uppercase small fw-bold text-info" style="font-size:10px; letter-spacing:1px;">
            Administrators
          </span>
          <div class="fs-4 fw-bold">1</div>''', '''          <span class="text-uppercase small fw-bold text-info" style="font-size:10px; letter-spacing:1px;">
            Administrators
          </span>
          <div class="fs-4 fw-bold">{{ admins }}</div>''')

# 2. Table tbody
table_tbody = '''                <tbody class="fs-7 fw-medium text-black">
                    {% for staff in staffs %}
                    <tr>
                        <td>
                          <label class="fw-semibold text-black">{{ staff.name }}</label>
                        </td>
                        <td>
                          <label class="fs-8 fw-normal"><i class="ri-phone-line me-1"></i>{{ staff.mobile_number }}</label>
                        </td>
                        <td>
                          <label class="badge rounded ms-2" style="background: #ebe3f1; color: #5d0a72; border:1px solid #63085e;">{{ staff.role }}</label>
                        </td>
                        <td>
                          <div class="d-flex align-items-center flex-wrap gap-2">
                            {% for perm in staff.permissions|slice:":2" %}
                              <span class="badge bg-gray-100 text-dark border">
                                {{ perm }}
                              </span>
                            {% empty %}
                                <span class="text-muted">No specific permissions</span>
                            {% endfor %}
                            {% if staff.permissions|length > 2 %}
                              <div class="dropdown">
                                <button class="btn btn-sm btn-outline-secondary dropdown-toggle"
                                        type="button"
                                        data-bs-toggle="dropdown">
                                  +{{ staff.permissions|length|add:"-2" }} more
                                </button>
                                <ul class="dropdown-menu">
                                  {% for perm in staff.permissions|slice:"2:" %}
                                    <li>
                                      <span class="dropdown-item-text">
                                        {{ perm }}
                                      </span>
                                    </li>
                                  {% endfor %}
                                </ul>
                              </div>
                            {% endif %}
                          </div>
                        </td>
                        <td>
                            {% if staff.status == "0" %}
                            <span class="badge bg-label-primary text-primary rounded-3">Active</span>
                            {% else %}
                            <span class="badge bg-label-danger text-danger rounded-3">Suspended</span>
                            {% endif %}
                        </td>
                        <td class="text-end">
                            <button class="btn btn-sm btn-outline-secondary" data-bs-toggle="offcanvas" data-bs-target="#view_activity_offcanvas"><i class="ri-eye-line"></i></button>
                            <button class="btn btn-sm btn-outline-secondary edit-btn" 
                                    data-id="{{ staff.id }}"
                                    data-name="{{ staff.name }}"
                                    data-mobile="{{ staff.mobile_number }}"
                                    data-role="{{ staff.role }}"
                                    data-status="{{ staff.status }}"
                                    data-permissions="{{ staff.permissions|join:',' }}"
                                    data-bs-toggle="modal" data-bs-target="#edit_staff_modal"><i class="ri-pencil-line"></i></button>
                            <button class="btn btn-sm btn-outline-danger delete-btn"
                                    data-id="{{ staff.id }}"
                                    data-name="{{ staff.name }}"
                                    data-bs-toggle="modal"
                                    data-bs-target="#delete_staff_modal">
                                <i class="ri-delete-bin-7-line"></i>
                            </button>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>'''
# Find the start and end of tbody
start_tbody = content.find('<tbody class="fs-7 fw-medium text-black">')
end_tbody = content.find('</tbody>', start_tbody) + len('</tbody>')
content = content[:start_tbody] + table_tbody + content[end_tbody:]

# 3. Add Modal
start_add = content.find('id="add_staff_modal"')
add_modal_content_start = content.find('<div class="modal-content">', start_add)
content = content[:add_modal_content_start] + '<form class="modal-content" method="POST" action="{% url \'farmer-user\' %}">\n      {% csrf_token %}\n      <input type="hidden" name="action" value="create">' + content[add_modal_content_start+len('<div class="modal-content">'):]

# Replace inputs in Add Modal
start_add = content.find('id="add_staff_modal"')
end_add = content.find('id="edit_staff_modal"')
add_section = content[start_add:end_add]

add_section = add_section.replace('<input type="text" class="form-control border  rounded" placeholder="Liam Sterling">', '<input type="text" name="name" class="form-control border rounded" placeholder="Liam Sterling" required>')
add_section = add_section.replace('<input type="text" class="form-control border  rounded" placeholder="+91 9876543210">', '<input type="text" name="mobile_number" class="form-control border rounded" placeholder="+91 9876543210" required>')
add_section = add_section.replace('<select id="roleSelect" class="form-select border rounded">', '<select id="roleSelect" name="role" class="form-select border rounded" required>')
add_section = add_section.replace('<select class="select3 form-select border  rounded" placeholder="Select Status">', '<select name="status" class="form-select border rounded" required>')
add_section = add_section.replace('<input class="form-check-input mt-1" type="checkbox"', '<input class="form-check-input mt-1" type="checkbox" name="permissions[]"')

# Assign values to checkboxes
add_section = add_section.replace('name="permissions[]">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Access Billing & Subscriptions', 'name="permissions[]" value="Access Billing & Subscriptions">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Access Billing & Subscriptions')
add_section = add_section.replace('name="permissions[]">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Create & Edit Farming Guidelines', 'name="permissions[]" value="Create & Edit Farming Guidelines">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Create & Edit Farming Guidelines')
add_section = add_section.replace('name="permissions[]" checked>\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Reconcile Worker & Rental Listings', 'name="permissions[]" value="Reconcile Worker & Rental Listings" checked>\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Reconcile Worker & Rental Listings')
add_section = add_section.replace('name="permissions[]" checked>\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Configure IoT Nodes & System Alerts', 'name="permissions[]" value="Configure IoT Nodes & System Alerts" checked>\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Configure IoT Nodes & System Alerts')
add_section = add_section.replace('name="permissions[]">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Full Root Administrative Override', 'name="permissions[]" value="Full Root Administrative Override">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Full Root Administrative Override')

# Fix Add form closing tag
# Find the end of modal content (the third </div> from the bottom of add_section)
# Actually, the button is "Confirm Registration"
btn_pos = add_section.find('Confirm Registration')
btn_end = add_section.find('</button>', btn_pos) + len('</button>')
footer_end = add_section.find('</div>', btn_end) + len('</div>')
add_section = add_section[:footer_end] + '\n    </form>' + add_section[footer_end+len('\n    </div>'):]
add_section = add_section.replace('<button type="button" class="btn btn-primary">\n          <i class="ri-check-line me-1"></i> Confirm Registration\n        </button>', '<button type="submit" class="btn btn-primary">\n          <i class="ri-check-line me-1"></i> Confirm Registration\n        </button>')
content = content[:start_add] + add_section + content[end_add:]

# 4. Edit Modal
start_edit = content.find('id="edit_staff_modal"')
end_edit = content.find('id="delete_staff_modal"')
edit_section = content[start_edit:end_edit]

edit_modal_content_start = edit_section.find('<div class="modal-content">')
edit_section = edit_section[:edit_modal_content_start] + '<form class="modal-content" method="POST" action="{% url \'farmer-user\' %}">\n      {% csrf_token %}\n      <input type="hidden" name="action" value="update">\n      <input type="hidden" name="staff_id" id="editStaffId">' + edit_section[edit_modal_content_start+len('<div class="modal-content">'):]

edit_section = edit_section.replace('<input value="Liam Sterling" type="text" class="form-control border  rounded">', '<input type="text" name="name" id="editName" class="form-control border rounded" required>')
edit_section = edit_section.replace('<input value="+91 9876543210" type="text" class="form-control border rounded">', '<input type="text" name="mobile_number" id="editMobile" class="form-control border rounded" required>')
edit_section = edit_section.replace('<select class="select3 form-select border rounded" placeholder="Administrator">', '<select name="role" id="editRole" class="form-select border rounded" required>')
edit_section = edit_section.replace('<select class="select3 form-select border rounded" placeholder="Select Status">', '<select name="status" id="editStatus" class="form-select border rounded" required>')

edit_section = edit_section.replace('<input class="form-check-input mt-1" type="checkbox"', '<input class="form-check-input mt-1 edit-perm-chk" type="checkbox" name="permissions[]"')

edit_section = edit_section.replace('name="permissions[]">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Access Billing & Subscriptions', 'name="permissions[]" value="Access Billing & Subscriptions">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Access Billing & Subscriptions')
edit_section = edit_section.replace('name="permissions[]">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Create & Edit Farming Guidelines', 'name="permissions[]" value="Create & Edit Farming Guidelines">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Create & Edit Farming Guidelines')
edit_section = edit_section.replace('name="permissions[]" checked>\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Reconcile Worker & Rental Listings', 'name="permissions[]" value="Reconcile Worker & Rental Listings">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Reconcile Worker & Rental Listings')
edit_section = edit_section.replace('name="permissions[]" checked>\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Configure IoT Nodes & System Alerts', 'name="permissions[]" value="Configure IoT Nodes & System Alerts">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Configure IoT Nodes & System Alerts')
edit_section = edit_section.replace('name="permissions[]">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Full Root Administrative Override', 'name="permissions[]" value="Full Root Administrative Override">\n              <div class="min-w-0">\n                <div class="fw-bold" style="font-size:11px;">\n                  Full Root Administrative Override')

btn_pos = edit_section.find('Confirm Registration')
btn_end = edit_section.find('</button>', btn_pos) + len('</button>')
footer_end = edit_section.find('</div>', btn_end) + len('</div>')
edit_section = edit_section[:footer_end] + '\n    </form>' + edit_section[footer_end+len('\n    </div>'):]
edit_section = edit_section.replace('<button type="button" class="btn btn-primary">\n          <i class="ri-check-line me-1"></i> Confirm Registration\n        </button>', '<button type="submit" class="btn btn-primary">\n          <i class="ri-check-line me-1"></i> Update Registration\n        </button>')
content = content[:start_edit] + edit_section + content[end_edit:]

# 5. Delete Modal
start_del = content.find('id="delete_staff_modal"')
end_del = content.find('id="view_activity_offcanvas"')
del_section = content[start_del:end_del]
del_modal_content_start = del_section.find('<div class="modal-content">')
del_section = del_section[:del_modal_content_start] + '<form class="modal-content" method="POST" action="{% url \'farmer-user\' %}">\n      {% csrf_token %}\n      <input type="hidden" name="action" value="delete">\n      <input type="hidden" name="staff_id" id="deleteStaffId">' + del_section[del_modal_content_start+len('<div class="modal-content">'):]

btn_pos = del_section.find('id="confirmDeleteBtn"')
btn_end = del_section.find('</button>', btn_pos) + len('</button>')
footer_end = del_section.find('</div>', btn_end) + len('</div>')
del_section = del_section[:footer_end] + '\n    </form>' + del_section[footer_end+len('\n    </div>'):]
del_section = del_section.replace('<button type="button" class="btn btn-danger" id="confirmDeleteBtn">\n          Yes, Delete\n        </button>', '<button type="submit" class="btn btn-danger" id="confirmDeleteBtn">\n          Yes, Delete\n        </button>')
content = content[:start_del] + del_section + content[end_del:]

# Append JS listener for the Edit and Delete buttons
js_injection = r'''
<script>
  document.addEventListener('DOMContentLoaded', function() {
    // Edit Modal Binding
    const editBtns = document.querySelectorAll('.edit-btn');
    editBtns.forEach(btn => {
      btn.addEventListener('click', function() {
        document.getElementById('editStaffId').value = this.dataset.id;
        document.getElementById('editName').value = this.dataset.name;
        document.getElementById('editMobile').value = this.dataset.mobile;
        
        let roleSelect = document.getElementById('editRole');
        let roleFound = false;
        for(let i=0; i<roleSelect.options.length; i++) {
            if(roleSelect.options[i].value === this.dataset.role) {
                roleSelect.selectedIndex = i;
                roleFound = true;
                break;
            }
        }
        if(!roleFound) {
            let option = document.createElement('option');
            option.value = this.dataset.role;
            option.text = this.dataset.role;
            option.selected = true;
            roleSelect.appendChild(option);
        }
        
        document.getElementById('editStatus').value = this.dataset.status;
        
        const perms = this.dataset.permissions ? this.dataset.permissions.split(',') : [];
        const chks = document.querySelectorAll('.edit-perm-chk');
        chks.forEach(chk => {
            chk.checked = perms.includes(chk.value);
        });
      });
    });

    // Delete Modal Binding
    const deleteBtns = document.querySelectorAll('.delete-btn');
    deleteBtns.forEach(btn => {
      btn.addEventListener('click', function() {
        document.getElementById('deleteStaffId').value = this.dataset.id;
        document.getElementById('deleteFarmerName').textContent = this.dataset.name;
      });
    });
  });
</script>
'''

if '<!-- Append JS -->' not in content:
    content = content.replace('{% endblock page_js %}', f'{js_injection}\n<!-- Append JS -->\n{{% endblock page_js %}}')

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated HTML securely.")
