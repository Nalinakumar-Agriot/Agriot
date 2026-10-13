from django.urls import path
from . import views
from apps.superadmin.views import login_view as superadmin_login

urlpatterns = [
    # Auth / base
    path('', superadmin_login, name='home'),
    path('logout/', views.logout_view, name='logout'),
    path('update-profile/', views.update_my_profile, name='update_my_profile'),

    # Dashboard
    path('dashboard/', views.dashboard, name='superadmin-dashboard'),

    # Farmer Management
    path('farmers/', views.manage_farmer, name='manage_farmer'),

    # Reports Hub & Individual Reports
    path('reports/', views.reports_dashboard, name='superadmin_reports'),
    path('reports/customer-report/', views.customer_report, name='customer_report'),
    path('reports/land-report/', views.land_report, name='land_report'),
    path('reports/expense-report/', views.expense_report, name='expense_report'),
    path('reports/vendor-report/', views.vendor_report, name='vendor_report'),
    path('reports/salary-report/', views.salary_report, name='salary_report'),
    path('reports/attendance-report/', views.attendance_report, name='attendance_report'),
    path('reports/advance-report/', views.advance_report, name='advance_report'),

    # Package Management
    path('packages/', views.manage_package, name='manage_package'),
    path('packages/revenue-report/', views.manage_package_revenue, name='manage_package_revenue'),

    # Subscription
    path('subscriptions/', views.manage_subscription, name='manage_subscription'),

    # Market
    path('market/', views.manage_market, name='manage_market'),
    path('market-price/', views.manage_market_price, name='manage_market_price'),
    path('market/quick-price-update/', views.quick_price_update, name='quick_price_update'),
    path('market/ajax-quick-add-crop/', views.ajax_quick_add_crop, name='ajax_quick_add_crop'),
    path('market/ajax-get-price-history/', views.get_price_history_ajax, name='get_price_history_ajax'),
    # Schedule
    path('schedule/', views.manage_schedule, name='manage_schedule'),
    path('schedule/ajax-quick-add-category/', views.ajax_quick_add_guideline_category, name='ajax_quick_add_guideline_category'),
    path('schedule/ajax-quick-delete-category/', views.ajax_quick_delete_guideline_category, name='ajax_quick_delete_guideline_category'),

    # Blueprint
    path('schedule/blueprint/add/', views.add_crop_blueprint, name='add_crop_blueprint'),
    path('schedule/blueprint/delete/', views.delete_crop_blueprint, name='delete_crop_blueprint'),
    path('schedule/blueprint/stage/add/', views.add_blueprint_stage, name='add_blueprint_stage'),
    path('schedule/blueprint/stage/edit/', views.edit_blueprint_stage, name='edit_blueprint_stage'),
    path('schedule/blueprint/stage/delete/', views.delete_blueprint_stage, name='delete_blueprint_stage'),


    # Nearby locations
    path('nearby/', views.manage_nearby, name='manage_nearby'),
    path('nearby/ajax-quick-add-service-class/', views.ajax_quick_add_service_class, name='ajax_quick_add_service_class'),
    path('nearby/ajax-quick-delete-service-class/', views.ajax_quick_delete_service_class, name='ajax_quick_delete_service_class'),

    # Workers
    path('workers/', views.manage_workers_availability, name='manage_workers_availability'),
    path('workers/ajax-quick-add-designation/', views.ajax_quick_add_designation, name='ajax_quick_add_designation'),
    path('workers/ajax-quick-delete-designation/', views.ajax_quick_delete_designation, name='ajax_quick_delete_designation'),
    path('workers/ajax-quick-add-skill/', views.ajax_quick_add_worker_skill, name='ajax_quick_add_worker_skill'),
    path('workers/ajax-quick-delete-skill/', views.ajax_quick_delete_worker_skill, name='ajax_quick_delete_worker_skill'),
    path('workers/ajax-quick-add-wage-unit/', views.ajax_quick_add_wage_unit, name='ajax_quick_add_wage_unit'),
    path('workers/ajax-quick-delete-wage-unit/', views.ajax_quick_delete_wage_unit, name='ajax_quick_delete_wage_unit'),

    # Rental
    path('rentals/', views.manage_rental_availability, name='manage_rental_availability'),

    # Guidelines
    path('guidelines/', views.manage_guidelines, name='manage_guidelines'),

    # Document Management
    path('documents/', views.manage_documents, name='superadmin-documents'),
    path('documents/share/<uuid:token>/', views.share_document_view, name='superadmin-share-document'),

    # Users
    path('users/', views.manage_users, name='manage_users'),

    # Settings
    path('general_settings/', views.general_settings, name="general_settings"),
    path('ajax/get-states/', views.ajax_get_states, name="ajax_get_states"),
    path('ajax/get-cities/', views.ajax_get_cities, name="ajax_get_cities"),
    path('ajax/get-taluks/', views.ajax_get_taluks, name="ajax_get_taluks"),
    path('product_unit/', views.product_unit, name="product_unit"),
    path('soil_type/', views.soil_type, name="soil_type"),
    path('crop_type/', views.crop_type, name="crop_type"),
    path('crop_settings/', views.crop_settings, name="crop_settings"),
    path('sales_deduction/', views.sales_deduction, name="sales_deduction"),
    path('schedule_activity/', views.schedule_activity, name="schedule_activity"),
    path('location_category/', views.location_category, name="location_category"),
    path('man_power/', views.man_power, name="man_power"),
    path('guideline_category/', views.guideline_category, name="guideline_category"),
    path('document_category/', views.document_category, name="document_category"),
    path('product_type/', views.product_type, name="product_type"),
    path('inventory_item/', views.inventory_item, name="inventory_item"),
    path('inventory_category/', views.inventory_category_settings, name="inventory_category_settings"),
    path('storage_location/', views.storage_location_settings, name="storage_location_settings"),
    path('harvesting_frequency/', views.harvesting_frequency, name="harvesting_frequency"),
    path('hardware_class_settings/', views.hardware_class_settings, name="hardware_class_settings"),
    path('expense_type/', views.expense_type_settings, name="expense_type_settings"),
    path('expense_unit/', views.expense_unit_settings, name="expense_unit_settings"),
    path('designation/', views.designation_settings, name="designation_settings"),
    path('customer_type/', views.customer_type_settings, name="customer_type_settings"),
    path('task_category/', views.task_category_settings, name="task_category_settings"),
    path('task_priority/', views.task_priority_settings, name="task_priority_settings"),
    path('irrigation_type/', views.irrigation_type, name="irrigation_type"),
    path('water_source_type/', views.water_source_type, name="water_source_type"),
    path('electricity_type/', views.electricity_type, name="electricity_type"),
    path('farming_type/', views.farming_type, name="farming_type"),
    path('fencing_type/', views.fencing_type, name="fencing_type"),
    path('crop_category/', views.crop_category_settings, name="crop_category_settings"),
    path('crop_pattern/', views.crop_pattern_settings, name="crop_pattern_settings"),
    path('crop_variety/', views.crop_variety_settings, name="crop_variety_settings"),
    path('crop_quality/', views.crop_quality_settings, name="crop_quality_settings"),
    path('maintenance_schedule/', views.maintenance_schedule_settings, name="maintenance_schedule_settings"),

    # Auth flows
    path('forgot-password/', views.forgot_password, name='forgot-password'),
    path('verify-otp/', views.verify_otp, name='verify-otp'),
    path('reset-password/', views.reset_password, name='reset-password'),
]
