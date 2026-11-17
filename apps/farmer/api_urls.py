from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.farmer.api_views import (
    FarmerRegistrationAPIView, FarmerRegistrationVerifyOTPAPIView,
    FarmerSendOTPAPIView, FarmerVerifyOTPAPIView, FarmerProfileAPIView,
    FarmerLogoutAPIView, FarmerSubscriptionAPIView, FarmerSubscriptionHistoryAPIView, 
    FarmerAvailableAcresAPIView,
    FarmerSwitchPlanAPIView, FarmerVerifyPaymentAPIView,
    ManagerPermissionsAPIView,
    PackageListAPIView, FarmListCreateAPIView,
    FarmRetrieveUpdateDestroyAPIView, FarmDashboardAPIView, MobileDashboardAPIView,
    MobileDashboardOverviewAPIView, MobileDashboardAnalyticsAPIView,
    MobileDashboardOperationsAPIView, MobileDashboardMarketAPIView,
    CropTypeAPIView, CropAPIView, StateAPIView, DistrictAPIView, TalukAPIView,
    PublicStateAPIView, PublicDistrictAPIView, PublicTalukAPIView,
    CropSplitViewSet, FarmActivityViewSet, FarmTaskViewSet,
    LedgerTransactionViewSet, FarmerInventoryViewSet,
    FarmerStaffViewSet, StaffAttendanceViewSet, StaffAdvanceViewSet,
    StaffPayrollViewSet,
    MarketPriceIndexViewSet, DepotTerminalViewSet, DepotCropPricingViewSet,
    DailyPriceHistoryViewSet,
    NearbyLocationViewSet, LogisticsWorkerViewSet, RentalEquipmentViewSet,
    CropBlueprintViewSet, BlueprintStageViewSet, GuidelineViewSet, GuidelineCategoryViewSet,
    ConsumptionViewSet, FarmExpenseViewSet,
    InventoryDropdownAPIView, ExpenseDropdownAPIView, DesignationAPIView, QuickAddDesignationAPIView,
    ServiceClassListAPIView, QuickAddServiceClassAPIView, WorkTypeListAPIView, QuickAddWorkTypeAPIView,
    QuickAddCropAPIView, QuickAddSoilTypeAPIView, QuickAddIrrigationTypeAPIView,
    QuickAddWaterSourceAPIView, QuickAddElectricityTypeAPIView, QuickAddFarmingTypeAPIView,
    QuickAddFencingTypeAPIView, QuickAddMaintenanceScheduleAPIView, QuickAddDocumentTypeAPIView,
    QuickAddCropCategoryAPIView, QuickAddCropPatternAPIView, QuickAddCropVarietyAPIView, QuickAddCropQualityAPIView,
    FarmerCustomerViewSet, FarmerVendorViewSet, FarmerOutstandingViewSet,
    CropSellAPIView,
    YieldProcessStatusAPIView, YieldStageAdvanceAPIView,
    YieldHarvestFinalizeAPIView, YieldProcessSummaryAPIView,
    FarmDocumentUploadAPIView,
    DeactivateLandAPIView, DeactivateScheduleAPIView, DeleteCropAPIView,
    CropDetailsLandAPIView, CropViewDetailAPIView, EditMyLandAPIView,
    TaskListForMonthCropAPIView, TasksForMonthCropAPIView,
    HarvestedCropsAPIView, CustomerTypeListAPIView, SaleCreateAPIView,
    QuickAddLocationAPIView, QuickAddInventoryTypeAPIView, QuickAddInventoryCategoryAPIView,
    InventoryDropdownOptionsAPIView,
    SoilTypeListAPIView, IrrigationTypeListAPIView, WaterSourceTypeListAPIView,
    ElectricityTypeListAPIView, FarmingTypeListAPIView, FencingTypeListAPIView,
    MaintenanceScheduleListAPIView, DocumentTypeListAPIView,
    CropCategoryListAPIView, CropPatternListAPIView, CropVarietyListAPIView,
    CropQualityListAPIView, YieldUnitListAPIView,
    TaskCategoryListAPIView, TaskPriorityListAPIView,
    QuickAddTaskCategoryAPIView, QuickAddTaskPriorityAPIView,
    QuickAddExpenseTypeAPIView, QuickAddExpenseUnitAPIView, QuickAddExpenseVendorAPIView,
    FarmerDocumentViewSet,
    NotificationListView, NotificationCountView, NotificationReadAllView,
    NotificationReadView, NotificationClearAllView,
    LandAndCropDetailsAPIView, LandGeoMarksAPIView, CropSummaryMapViewAPIView,
    AvailableProfilesAPIView, SwitchProfileAPIView,
    HarvestStockListAPIView, RecordCropLossAPIView, HarvestLossHistoryAPIView,
)

router = DefaultRouter()
# ── Farms & Crops ──
router.register(r'crop-splits', CropSplitViewSet, basename='api-farmer-cropsplit')
router.register(r'activities', FarmActivityViewSet, basename='api-farmer-activity')
router.register(r'consumption', ConsumptionViewSet, basename='api-farmer-consumption')
router.register(r'tasks', FarmTaskViewSet, basename='api-farmer-task')
# ── Financials ──
router.register(r'expenses', FarmExpenseViewSet, basename='api-farmer-expenses')
router.register(r'transactions', LedgerTransactionViewSet, basename='api-farmer-transaction')
# ── Inventory ──
router.register(r'inventory', FarmerInventoryViewSet, basename='api-farmer-inventory')
# ── Customers & Vendors ──
router.register(r'customers', FarmerCustomerViewSet, basename='api-farmer-customers')
router.register(r'vendors', FarmerVendorViewSet, basename='api-farmer-vendors')
router.register(r'outstanding', FarmerOutstandingViewSet, basename='api-farmer-outstanding')
# ── My Documents ──
router.register(r'documents', FarmerDocumentViewSet, basename='api-farmer-documents')
# ── Employees ──
router.register(r'users', FarmerStaffViewSet, basename='api-farmer-staff')
router.register(r'attendance', StaffAttendanceViewSet, basename='api-farmer-attendance')
router.register(r'advances', StaffAdvanceViewSet, basename='api-farmer-advances')
router.register(r'payrolls', StaffPayrollViewSet, basename='api-farmer-payrolls')
# ── Market Hub ──
router.register(r'market-prices', MarketPriceIndexViewSet, basename='api-farmer-market-prices')
router.register(r'daily-price-history', DailyPriceHistoryViewSet, basename='api-farmer-daily-price-history')
router.register(r'depots', DepotTerminalViewSet, basename='api-farmer-depots')
router.register(r'depot-pricing', DepotCropPricingViewSet, basename='api-farmer-depot-pricing')
router.register(r'locations', NearbyLocationViewSet, basename='api-farmer-locations')
router.register(r'labour', LogisticsWorkerViewSet, basename='api-farmer-labour')
router.register(r'equipment', RentalEquipmentViewSet, basename='api-farmer-equipment')
# ── Smart Farming ──
router.register(r'blueprints', CropBlueprintViewSet, basename='api-farmer-blueprints')
router.register(r'blueprint-stages', BlueprintStageViewSet, basename='api-farmer-blueprint-stages')
router.register(r'guideline-categories', GuidelineCategoryViewSet, basename='api-farmer-guideline-categories')
router.register(r'guidelines', GuidelineViewSet, basename='api-farmer-guidelines')

urlpatterns = [
    # ── Inventory Quick Add & Stock Log (before router to avoid pk conflict) ──
    path('inventory/quick-add-location/', QuickAddLocationAPIView.as_view(), name='api-farmer-quick-add-location'),
    path('inventory/quick-add-type/', QuickAddInventoryTypeAPIView.as_view(), name='api-farmer-quick-add-type'),
    path('inventory/quick-add-category/', QuickAddInventoryCategoryAPIView.as_view(), name='api-farmer-quick-add-category'),
    path('inventory/dropdown-options/', InventoryDropdownOptionsAPIView.as_view(), name='api-farmer-inventory-dropdown-options'),
    path('inventory/harvest-stock/', HarvestStockListAPIView.as_view(), name='api-farmer-harvest-stock'),
    path('inventory/harvest-loss/', RecordCropLossAPIView.as_view(), name='api-farmer-harvest-loss'),
    path('inventory/harvest-loss/history/', HarvestLossHistoryAPIView.as_view(), name='api-farmer-harvest-loss-history'),

    path('', include(router.urls)),
    # Auth
    path('register/', FarmerRegistrationAPIView.as_view(), name='api-farmer-register'),
    path('send_otp/', FarmerRegistrationAPIView.as_view(), name='api-farmer-registration-send-otp'),
    path('verify_otp_and_payment/', FarmerRegistrationVerifyOTPAPIView.as_view(), name='api-farmer-registration-verify-otp'),
    path('verify_payment/', FarmerVerifyPaymentAPIView.as_view(), name='api-farmer-registration-verify-payment'),
    # Registration location dropdowns (public - no JWT required)
    path('registration/states/', PublicStateAPIView.as_view(), name='api-farmer-registration-states'),
    path('registration/districts/', PublicDistrictAPIView.as_view(), name='api-farmer-registration-districts'),
    path('registration/taluks/', PublicTalukAPIView.as_view(), name='api-farmer-registration-taluks'),
    path('login/send-otp/', FarmerSendOTPAPIView.as_view(), name='api-farmer-send-otp'),
    path('login/verify-otp/', FarmerVerifyOTPAPIView.as_view(), name='api-farmer-verify-otp'),
    path('logout/', FarmerLogoutAPIView.as_view(), name='api-farmer-logout'),
    path('manager-permissions/', ManagerPermissionsAPIView.as_view(), name='api-farmer-manager-permissions'),
    path('available-profiles/', AvailableProfilesAPIView.as_view(), name='api-farmer-available-profiles'),
    path('switch-profile/', SwitchProfileAPIView.as_view(), name='api-farmer-switch-profile'),
    # Profile
    path('profile/', FarmerProfileAPIView.as_view(), name='api-farmer-profile'),
    path('subscription/', FarmerSubscriptionAPIView.as_view(), name='api-farmer-subscription'),
    path('subscription/available-acres/', FarmerAvailableAcresAPIView.as_view(), name='api-farmer-available-acres'),
    path('subscription/history/', FarmerSubscriptionHistoryAPIView.as_view(), name='api-farmer-subscription-history'),
    path('subscription/switch-plan/', FarmerSwitchPlanAPIView.as_view(), name='api-farmer-switch-plan'),
    path('subscription/verify-payment/', FarmerVerifyPaymentAPIView.as_view(), name='api-farmer-verify-payment'),
    path('packages/', PackageListAPIView.as_view(), name='api-farmer-packages'),
    # Farms
    path('farms/', FarmListCreateAPIView.as_view(), name='api-farmer-farms'),
    path('farms/<int:pk>/', FarmRetrieveUpdateDestroyAPIView.as_view(), name='api-farmer-farm-detail'),
    path('farms/<int:pk>/dashboard/', FarmDashboardAPIView.as_view(), name='api-farmer-farm-dashboard'),
    # Crop Sell (full logic with ledger + outstanding)
    path('crop-splits/<int:pk>/sell/', CropSellAPIView.as_view(), name='api-farmer-crop-sell'),
    path('crop-splits/sell/', CropSellAPIView.as_view(), name='api-farmer-crop-sell-direct'),
    path('farms/sell_crop_split/', CropSellAPIView.as_view(), name='api-farmer-farm-sell-crop-split'),
    path('crop-splits/<int:pk>/profile/', CropSplitViewSet.as_view({'get': 'profile'}), name='api-farmer-crop-profile'),
    # Dropdowns
    path('dropdowns/crop-types/', CropTypeAPIView.as_view(), name='api-farmer-croptypes'),
    path('dropdowns/crops/', CropAPIView.as_view(), name='api-farmer-crops'),
    path('dropdowns/states/', StateAPIView.as_view(), name='api-farmer-states'),
    path('dropdowns/districts/', DistrictAPIView.as_view(), name='api-farmer-districts'),
    path('dropdowns/taluks/', TalukAPIView.as_view(), name='api-farmer-taluks'),
    path('dropdowns/soil-types/', SoilTypeListAPIView.as_view(), name='api-farmer-soil-types'),
    path('dropdowns/irrigation-types/', IrrigationTypeListAPIView.as_view(), name='api-farmer-irrigation-types'),
    path('dropdowns/water-source-types/', WaterSourceTypeListAPIView.as_view(), name='api-farmer-water-source-types'),
    path('dropdowns/electricity-types/', ElectricityTypeListAPIView.as_view(), name='api-farmer-electricity-types'),
    path('dropdowns/farming-types/', FarmingTypeListAPIView.as_view(), name='api-farmer-farming-types'),
    path('dropdowns/fencing-types/', FencingTypeListAPIView.as_view(), name='api-farmer-fencing-types'),
    path('dropdowns/maintenance-schedules/', MaintenanceScheduleListAPIView.as_view(), name='api-farmer-maintenance-schedules'),
    path('dropdowns/document-types/', DocumentTypeListAPIView.as_view(), name='api-farmer-document-types'),
    path('dropdowns/crop-categories/', CropCategoryListAPIView.as_view(), name='api-farmer-crop-categories'),
    path('dropdowns/crop-patterns/', CropPatternListAPIView.as_view(), name='api-farmer-crop-patterns'),
    path('dropdowns/crop-varieties/', CropVarietyListAPIView.as_view(), name='api-farmer-crop-varieties'),
    path('dropdowns/crop-qualities/', CropQualityListAPIView.as_view(), name='api-farmer-crop-qualities'),
    path('dropdowns/yield-units/', YieldUnitListAPIView.as_view(), name='api-farmer-yield-units'),
    path('dropdowns/task-categories/', TaskCategoryListAPIView.as_view(), name='api-farmer-task-categories'),
    path('dropdowns/task-priorities/', TaskPriorityListAPIView.as_view(), name='api-farmer-task-priorities'),
    path('dropdowns/consumption-utilities/', InventoryDropdownAPIView.as_view(), name='api-farmer-consumption-utilities'),
    path('dropdowns/expenses/', ExpenseDropdownAPIView.as_view(), name='api-farmer-expenses-dropdown'),
    path('expenses/summary/', FarmExpenseViewSet.as_view({'get': 'summary'}), name='api-farmer-expenses-summary'),
    path('sales/summary/', LedgerTransactionViewSet.as_view({'get': 'summary'}), name='api-farmer-sales-summary'),
    path('dropdowns/designations/', DesignationAPIView.as_view(), name='api-farmer-designations'),
    path('dropdowns/quick-add-designation/', QuickAddDesignationAPIView.as_view(), name='api-farmer-quick-add-designation'),
    path('dropdowns/service-classes/', ServiceClassListAPIView.as_view(), name='api-farmer-service-classes'),
    path('dropdowns/quick-add-service-class/', QuickAddServiceClassAPIView.as_view(), name='api-farmer-quick-add-service-class'),
    path('dropdowns/work-types/', WorkTypeListAPIView.as_view(), name='api-farmer-work-types'),
    path('dropdowns/quick-add-work-type/', QuickAddWorkTypeAPIView.as_view(), name='api-farmer-quick-add-work-type'),
    path('dropdowns/quick-add-crop/', QuickAddCropAPIView.as_view(), name='api-farmer-quick-add-crop'),
    path('dropdowns/quick-add-soil-type/', QuickAddSoilTypeAPIView.as_view(), name='api-farmer-quick-add-soil-type'),
    path('dropdowns/quick-add-irrigation-type/', QuickAddIrrigationTypeAPIView.as_view(), name='api-farmer-quick-add-irrigation-type'),
    path('dropdowns/quick-add-water-source/', QuickAddWaterSourceAPIView.as_view(), name='api-farmer-quick-add-water-source'),
    path('dropdowns/quick-add-electricity-type/', QuickAddElectricityTypeAPIView.as_view(), name='api-farmer-quick-add-electricity-type'),
    path('dropdowns/quick-add-farming-type/', QuickAddFarmingTypeAPIView.as_view(), name='api-farmer-quick-add-farming-type'),
    path('dropdowns/quick-add-fencing-type/', QuickAddFencingTypeAPIView.as_view(), name='api-farmer-quick-add-fencing-type'),
    path('dropdowns/quick-add-maintenance-schedule/', QuickAddMaintenanceScheduleAPIView.as_view(), name='api-farmer-quick-add-maintenance-schedule'),
    path('dropdowns/quick-add-document-type/', QuickAddDocumentTypeAPIView.as_view(), name='api-farmer-quick-add-document-type'),
    path('dropdowns/quick-add-crop-category/', QuickAddCropCategoryAPIView.as_view(), name='api-farmer-quick-add-crop-category'),
    path('dropdowns/quick-add-crop-pattern/', QuickAddCropPatternAPIView.as_view(), name='api-farmer-quick-add-crop-pattern'),
    path('dropdowns/quick-add-crop-variety/', QuickAddCropVarietyAPIView.as_view(), name='api-farmer-quick-add-crop-variety'),
    path('dropdowns/quick-add-crop-quality/', QuickAddCropQualityAPIView.as_view(), name='api-farmer-quick-add-crop-quality'),
    path('dropdowns/quick-add-task-category/', QuickAddTaskCategoryAPIView.as_view(), name='api-farmer-quick-add-task-category'),
    path('dropdowns/quick-add-task-priority/', QuickAddTaskPriorityAPIView.as_view(), name='api-farmer-quick-add-task-priority'),
    path('dropdowns/quick-add-expense-type/', QuickAddExpenseTypeAPIView.as_view(), name='api-farmer-quick-add-expense-type'),
    path('dropdowns/quick-add-expense-unit/', QuickAddExpenseUnitAPIView.as_view(), name='api-farmer-quick-add-expense-unit'),
    path('dropdowns/quick-add-expense-vendor/', QuickAddExpenseVendorAPIView.as_view(), name='api-farmer-quick-add-expense-vendor'),
    path('farms/upload-document/', FarmDocumentUploadAPIView.as_view(), name='api-farmer-upload-document'),
    # Yield Process Steps
    path('yield/summary/', YieldProcessSummaryAPIView.as_view(), name='api-farmer-yield-summary'),
    path('yield/<int:pk>/status/', YieldProcessStatusAPIView.as_view(), name='api-farmer-yield-status'),
    path('yield/<int:pk>/advance/', YieldStageAdvanceAPIView.as_view(), name='api-farmer-yield-advance'),
    path('yield/<int:pk>/finalize-harvest/', YieldHarvestFinalizeAPIView.as_view(), name='api-farmer-yield-finalize'),
    # ── Legacy Compatibility (Flutter forming module) ──
    path('deactivate_my_land/', DeactivateLandAPIView.as_view(), name='api-farmer-deactivate-land'),
    path('deactivate-my-schedule/', DeactivateScheduleAPIView.as_view(), name='api-farmer-deactivate-schedule'),
    path('delete_crop/', DeleteCropAPIView.as_view(), name='api-farmer-delete-crop'),
    path('crop_details_land/', CropDetailsLandAPIView.as_view(), name='api-farmer-crop-details-land'),
    path('crop_view/<int:land_id>/<int:crop_id>/', CropViewDetailAPIView.as_view(), name='api-farmer-crop-view'),
    path('edit_my_land/', EditMyLandAPIView.as_view(), name='api-farmer-edit-land'),
    path('tasks_list/for-month-and-crop/', TaskListForMonthCropAPIView.as_view(), name='api-farmer-task-list-month-crop'),
    path('tasks/for-month-and-crop/', TasksForMonthCropAPIView.as_view(), name='api-farmer-tasks-month-crop'),
    # ── Sales (Mobile - matching website form) ──
    path('sales/create/', SaleCreateAPIView.as_view(), name='api-farmer-sale-create'),
    path('dropdowns/harvested-crops/', HarvestedCropsAPIView.as_view(), name='api-farmer-harvested-crops'),
    path('dropdowns/customer-types/', CustomerTypeListAPIView.as_view(), name='api-farmer-customer-types'),
    # ── Mobile Dashboard ──
    path('dashboard/', MobileDashboardAPIView.as_view(), name='api-farmer-mobile-dashboard'),
    path('dashboard/overview/', MobileDashboardOverviewAPIView.as_view(), name='api-farmer-mobile-dashboard-overview'),
    path('dashboard/analytics/', MobileDashboardAnalyticsAPIView.as_view(), name='api-farmer-mobile-dashboard-analytics'),
    path('dashboard/operations/', MobileDashboardOperationsAPIView.as_view(), name='api-farmer-mobile-dashboard-operations'),
    path('dashboard/market/', MobileDashboardMarketAPIView.as_view(), name='api-farmer-mobile-dashboard-market'),
    # ── Inventory Quick Add & Stock Log ──
    path('inventory/quick-add-location/', QuickAddLocationAPIView.as_view(), name='api-farmer-quick-add-location'),
    path('inventory/quick-add-type/', QuickAddInventoryTypeAPIView.as_view(), name='api-farmer-quick-add-type'),
    path('inventory/quick-add-category/', QuickAddInventoryCategoryAPIView.as_view(), name='api-farmer-quick-add-category'),
    path('inventory/dropdown-options/', InventoryDropdownOptionsAPIView.as_view(), name='api-farmer-inventory-dropdown-options'),
    # Notifications (Mobile)
    path('notifications/', NotificationListView.as_view(), name='api-farmer-notifications'),
    path('notifications/unread-count/', NotificationCountView.as_view(), name='api-farmer-notifications-unread-count'),
    path('notifications/read-all/', NotificationReadAllView.as_view(), name='api-farmer-notifications-read-all'),
    path('notifications/clear-all/', NotificationClearAllView.as_view(), name='api-farmer-notifications-clear-all'),
    path('notifications/<int:pk>/read/', NotificationReadView.as_view(), name='api-farmer-notification-read'),
    # ── Map View (Mobile) ──
    path('land-and-crop-details/', LandAndCropDetailsAPIView.as_view(), name='api-farmer-land-and-crop-details'),
    path('lands/geo-marks/', LandGeoMarksAPIView.as_view(), name='api-farmer-lands-geo-marks'),
    path('crop_summary/<int:land_id>/<int:crop_id>/', CropSummaryMapViewAPIView.as_view(), name='api-farmer-crop-summary-map'),
]
