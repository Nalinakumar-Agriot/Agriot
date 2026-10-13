import os

file_path = "templates/farmer/manage_users.html"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update the View Activity Button attributes
old_btn = '<button class="btn btn-sm btn-outline-secondary view-activity-btn" data-name="{{ staff.name }}" data-role="{{ staff.role }}" data-bs-toggle="offcanvas" data-bs-target="#view_activity_offcanvas"><i class="ri-eye-line"></i></button>'
new_btn = '''<button class="btn btn-sm btn-outline-secondary view-activity-btn" 
                                    data-name="{{ staff.name }}" 
                                    data-role="{{ staff.role }}" 
                                    data-mobile="{{ staff.mobile_number }}"
                                    data-status="{{ staff.status }}"
                                    data-permissions="{{ staff.permissions|join:',' }}"
                                    data-bs-toggle="offcanvas" 
                                    data-bs-target="#view_activity_offcanvas"><i class="ri-eye-line"></i></button>'''
content = content.replace(old_btn, new_btn)

# 2. Update JS listener
old_js = '''    // View Activity Offcanvas Binding
    const viewActivityBtns = document.querySelectorAll('.view-activity-btn');
    viewActivityBtns.forEach(btn => {
      btn.addEventListener('click', function() {
        document.getElementById('auditStaffName').textContent = this.dataset.name + ' (' + this.dataset.role + ')';
      });
    });'''
new_js = '''    // View Activity Offcanvas Binding
    const viewActivityBtns = document.querySelectorAll('.view-activity-btn');
    viewActivityBtns.forEach(btn => {
      btn.addEventListener('click', function() {
        document.getElementById('auditStaffName').textContent = this.dataset.name + ' (' + this.dataset.role + ')';
        document.getElementById('viewStaffMobile').textContent = this.dataset.mobile;
        
        const statusSpan = document.getElementById('viewStaffStatus');
        if (this.dataset.status === '0') {
            statusSpan.className = 'badge bg-label-primary text-primary rounded-3';
            statusSpan.textContent = 'Active';
        } else {
            statusSpan.className = 'badge bg-label-danger text-danger rounded-3';
            statusSpan.textContent = 'Suspended';
        }
        
        const perms = this.dataset.permissions ? this.dataset.permissions.split(',') : [];
        const permsContainer = document.getElementById('viewStaffPermissions');
        permsContainer.innerHTML = '';
        if (perms.length > 0) {
            perms.forEach(p => {
                let badge = document.createElement('span');
                badge.className = 'badge bg-gray-100 text-dark border me-1 mb-1';
                badge.textContent = p;
                permsContainer.appendChild(badge);
            });
        } else {
            let span = document.createElement('span');
            span.className = 'text-muted small';
            span.textContent = 'No specific permissions assigned';
            permsContainer.appendChild(span);
        }
      });
    });'''
content = content.replace(old_js, new_js)

# 3. Replace the offcanvas content completely
start_offcanvas = content.find('<div class="offcanvas offcanvas-end"')
end_offcanvas = content.find('</div>\n\n\n{% endblock %}', start_offcanvas) + len('</div>')

offcanvas_html = '''<div class="offcanvas offcanvas-end" tabindex="-1" id="view_activity_offcanvas" style="width: 500px;">
  
  <!-- HEADER -->
  <div class="offcanvas-header border-bottom bg-gray-100">
    <div class="d-flex align-items-center gap-3">
      <div class="p-2 bg-primary-subtle text-primary rounded-3">
        <i class="ri-user-settings-line fs-5"></i>
      </div>
      <div>
        <h5 class="mb-0 fw-bold">Staff Details</h5>
        <small class="text-muted" id="auditStaffName">Liam Sterling (Billing Agent)</small>
      </div>
    </div>
    <button type="button" class="btn-close" data-bs-dismiss="offcanvas"></button>
  </div>
  
  <!-- BODY -->
  <div class="offcanvas-body p-4 d-flex flex-column gap-4">
    <div>
        <label class="text-uppercase small fw-bold text-muted mb-1" style="font-size:10px; letter-spacing:1px;">Mobile Number</label>
        <div class="fs-6 fw-semibold text-dark"><i class="ri-phone-line me-1"></i><span id="viewStaffMobile"></span></div>
    </div>
    <div>
        <label class="text-uppercase small fw-bold text-muted mb-1" style="font-size:10px; letter-spacing:1px;">Status</label>
        <div>
            <span id="viewStaffStatus" class="badge"></span>
        </div>
    </div>
    <div>
        <label class="text-uppercase small fw-bold text-muted mb-2" style="font-size:10px; letter-spacing:1px;">Assigned Permissions</label>
        <div id="viewStaffPermissions" class="d-flex flex-wrap gap-1">
        </div>
    </div>
  </div>
  
  <!-- FOOTER -->
  <div class="p-3 border-top bg-gray-100">
    <button class="btn btn-dark w-100" data-bs-dismiss="offcanvas">
      Close View
    </button>
  </div>
</div>'''
content = content[:start_offcanvas] + offcanvas_html + content[end_offcanvas:]

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated Offcanvas Content to show user details.")
