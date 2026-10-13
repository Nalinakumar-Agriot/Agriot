import os

file_path = "templates/farmer/manage_users.html"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Add IDs to the custom filter inputs and dropdowns
old_search = '<input type="text"\n                class="form-control"\n                placeholder="Search staff mambers by name or emails...">'
new_search = '<input type="text"\n                id="customSearchBox"\n                class="form-control"\n                placeholder="Search staff members by name or mobile...">'
content = content.replace(old_search, new_search)

# Status Dropdown Menu
old_status = '''            <button class="btn btn-outline-secondary dropdown-toggle w-100" type="button" data-bs-toggle="dropdown" id="roleDropdown">
              All Status
            </button>
            <!-- Dropdown Menu -->
            <ul class="dropdown-menu w-100">
              <li><a class="dropdown-item role-filter" data-role="0" href="#">All Status</a></li>
              <li><a class="dropdown-item role-filter" data-role="1" href="#">Active</a></li>
              <li><a class="dropdown-item role-filter" data-role="2" href="#">Suspended</a></li>
            </ul>'''
new_status = '''            <button class="btn btn-outline-secondary dropdown-toggle w-100" type="button" data-bs-toggle="dropdown" id="statusDropdownBtn">
              All Status
            </button>
            <!-- Dropdown Menu -->
            <ul class="dropdown-menu w-100">
              <li><a class="dropdown-item custom-status-filter" data-val="" href="#">All Status</a></li>
              <li><a class="dropdown-item custom-status-filter" data-val="Active" href="#">Active</a></li>
              <li><a class="dropdown-item custom-status-filter" data-val="Suspended" href="#">Suspended</a></li>
            </ul>'''
content = content.replace(old_status, new_status)

# Roles Dropdown Menu
old_roles = '''            <button class="btn btn-outline-secondary dropdown-toggle w-100" type="button" data-bs-toggle="dropdown" id="roleDropdown">
              All Roles
            </button>
            <!-- Dropdown Menu -->
            <ul class="dropdown-menu w-100">
              <li><a class="dropdown-item role-filter" data-role="0" href="#">All Roles</a></li>
              <li><a class="dropdown-item role-filter" data-role="1" href="#">Administrator</a></li>
              <li><a class="dropdown-item role-filter" data-role="2" href="#">Agronomist</a></li>
              <li><a class="dropdown-item role-filter" data-role="3" href="#">Field Manager</a></li>
              <li><a class="dropdown-item role-filter" data-role="4" href="#">Billing Agent</a></li>
            </ul>'''
new_roles = '''            <button class="btn btn-outline-secondary dropdown-toggle w-100" type="button" data-bs-toggle="dropdown" id="roleDropdownBtn">
              All Roles
            </button>
            <!-- Dropdown Menu -->
            <ul class="dropdown-menu w-100">
              <li><a class="dropdown-item custom-role-filter" data-val="" href="#">All Roles</a></li>
              <li><a class="dropdown-item custom-role-filter" data-val="Administrator" href="#">Administrator</a></li>
              <li><a class="dropdown-item custom-role-filter" data-val="Agronomist" href="#">Agronomist</a></li>
              <li><a class="dropdown-item custom-role-filter" data-val="Field Manager" href="#">Field Manager</a></li>
              <li><a class="dropdown-item custom-role-filter" data-val="Billing Agent" href="#">Billing Agent</a></li>
            </ul>'''
content = content.replace(old_roles, new_roles)

# Add DataTables Filtering JS logic
js_code = '''
    // DataTable Custom Filtering
    setTimeout(function() {
        if ($.fn.DataTable.isDataTable('.datatable')) {
            var table = $('.datatable').DataTable();
            
            // Hide the default DataTables search and length to use our custom ones
            $('.dataTables_filter').hide();
            $('.dataTables_length').hide();

            // Custom Search Box
            $('#customSearchBox').on('keyup', function() {
                table.search(this.value).draw();
            });

            // Custom Status Filter (Column index 4 is Status)
            $('.custom-status-filter').on('click', function(e) {
                e.preventDefault();
                var val = $(this).data('val');
                var text = $(this).text();
                $('#statusDropdownBtn').text(text);
                table.column(4).search(val).draw();
            });

            // Custom Role Filter (Column index 2 is Role)
            $('.custom-role-filter').on('click', function(e) {
                e.preventDefault();
                var val = $(this).data('val');
                var text = $(this).text();
                $('#roleDropdownBtn').text(text);
                table.column(2).search(val).draw();
            });
        }
    }, 500); // Small delay to ensure DataTable is initialized by agriot_custom.js
'''

# Find the place to inject JS
insert_pos = content.find('});\n</script>\n\n<!-- Append JS -->')
content = content[:insert_pos] + js_code + content[insert_pos:]

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated custom filters successfully.")
