package com.agriot.farmer.domain

import org.json.JSONObject

enum class Kind { TEXT, LONG, DECIMAL, INTEGER, DATE, TIME, PHONE, EMAIL, BOOLEAN, CHOICE, RELATION, MULTI }
data class Field(
    val key: String, val label: String, val kind: Kind = Kind.TEXT,
    val required: Boolean = false, val options: List<String> = emptyList(),
    val source: String = "", val parent: String = "", val parentQuery: String = "",
    val positive: Boolean = false, val nonNegative: Boolean = false,
    val default: String = "", val maxLength: Int = 255
)
data class Module(
    val key: String, val title: String, val group: String, val permission: String,
    val titleKey: String, val fields: List<Field> = emptyList(),
    val create: Boolean = true, val edit: Boolean = true, val delete: Boolean = false,
    val summary: List<String> = emptyList()
) {
    val path get() = key + "/"
    fun recordTitle(row: JSONObject): String =
        listOf(titleKey, titleKey + "_name", "name", "title", "description", "crop_name", "land_name", "staff_name")
            .firstNotNullOfOrNull { row.optString(it).takeIf { v -> v.isNotBlank() && v != "null" } }
            ?: title.removeSuffix("s") + " #" + row.optString("id")
}
fun t(k: String, l: String, required: Boolean = false) = Field(k,l,required=required)
fun d(k: String, l: String, required: Boolean = false, positive: Boolean = false, default: String = "") =
    Field(k,l,Kind.DECIMAL,required,positive=positive,nonNegative=true,default=default)
fun date(k: String, l: String, required: Boolean = false) = Field(k,l,Kind.DATE,required)
fun choice(k: String,l: String,vararg values: String,default: String = "") =
    Field(k,l,Kind.CHOICE,options=values.toList(),default=default)
fun rel(k: String,l: String,source: String,required: Boolean = false,parent: String="",query: String="") =
    Field(k,l,Kind.RELATION,required,source=source,parent=parent,parentQuery=query)
fun phone(k: String,l: String,required: Boolean = false) = Field(k,l,Kind.PHONE,required,maxLength=10)
val farm = rel("farm","Farm","farms/",true)
val staff = rel("staff","Employee","users/",true)
val payment = choice("payment_method","Payment method","Cash","UPI","Bank Transfer","Credit",default="Cash")
val priority = choice("priority","Priority","Low Priority","Medium Priority","High Priority",default="Medium Priority")

object Catalog {
    val modules = listOf(
        Module("farms","Farms","Farm operations","Farms","land_name", listOf(
            t("land_name","Farm name",true),d("total_area","Total area (acres)",true,true),
            rel("crop_type","Crop type","dropdowns/crop-types/"),
            rel("state","State","dropdowns/states/"),
            rel("district","District","dropdowns/districts/",parent="state",query="state_id"),
            rel("taluk","Taluk","dropdowns/taluks/",parent="district",query="district_id"),
            t("address","Address"),Field("pincode","Pincode",maxLength=6),
            d("latitude","Latitude").copy(nonNegative=false),d("longitude","Longitude").copy(nonNegative=false),t("soil_type","Soil type"),
            d("soil_ph","Soil pH"),t("irrigation_type","Irrigation type"),
            t("farming_type","Farming type"),t("organic_registration_number","Organic registration number"),
            t("fencing_type","Fencing type"),t("fencing_maintenance_schedule","Fencing maintenance schedule"),
            choice("health_indicator","Farm health","Good","Moderate","Poor",default="Good"),
            Field("notes","Notes",Kind.LONG,maxLength=2000)
        ),delete=true,summary=listOf("total_area","crop_count","allocated_acres","district_name","health_indicator")),
        Module("crop-splits","Crops","Farm operations","Farms","crop_name", listOf(
            farm,rel("crop","Crop","dropdowns/crops/",true),
            d("acreage","Allocated area (acres)",true,true),
            t("crop_category","Crop category"),t("crop_pattern","Crop pattern"),t("crop_variety","Crop variety"),
            choice("harvest_type","Harvest type","single","multiple",default="single"),
            Field("total_flushes","Planned harvest flushes",Kind.INTEGER,positive=true,default="1"),
            date("start_date","Sowing date",true),
            Field("growth_duration","Growth duration (days)",Kind.INTEGER,positive=true,default="90"),
            t("cultivation_method","Cultivation method"),
            Field("supervisors","Managers",Kind.MULTI,source="users/"),
            d("expected_yield","Expected yield",positive=true),
            choice("expected_yield_unit","Yield unit","Kg","Ton","Quintal",default="Kg"),
            Field("row_count","Rows",Kind.INTEGER,positive=true),
            Field("plant_count","Plants",Kind.INTEGER,positive=true),
            t("seed_details","Seed details"),t("plant_spacing","Plant spacing"),
            Field("crop_notes","Notes",Kind.LONG,maxLength=2000)
        ),delete=true,summary=listOf("farm_name","acreage","stage","start_date","expected_yield","expected_yield_unit")),
        Module("tasks","Tasks","Farm operations","Task","title", listOf(
            t("title","Task title",true),farm,
            rel("associated_split","Crop","crop-splits/",parent="farm",query="farm_id"),
            rel("task_category","Task category","dropdowns/task-categories/"),
            priority,choice("work_timeline","Work timeline","Full Day","Half Day","Hourly",default="Full Day"),
            date("task_date","Task date",true),date("start_date","Start date"),date("end_date","End date"),
            Field("start_time","Start time",Kind.TIME),Field("end_time","End time",Kind.TIME),
            Field("assigned_workers","Assigned employees",Kind.MULTI,source="users/"),
            choice("status","Status","Pending","In Progress","Completed",default="Pending"),
            Field("description","Description",Kind.LONG,maxLength=3000)
        ),delete=true,summary=listOf("farm_name","crop_name","task_date","priority","status")),
        Module("consumption","Input consumption","Farm operations","Inventory","asset_name", listOf(
            farm,rel("crop_split_id","Crop","crop-splits/",parent="farm",query="farm_id"),
            rel("inventory_item","Inventory item","inventory/",true),
            d("quantity","Quantity used",true,true),t("operator_name","Recorded by",true),
            Field("notes","Notes",Kind.LONG,maxLength=2000)
        ),edit=false,summary=listOf("quantity_metric","operator_name","created_at")),
        Module("activities","Farm activities","Farm operations","Farms","asset_name", listOf(
            farm,t("activity_type","Activity type",true),t("asset_name","Asset or activity",true),
            t("quantity_metric","Quantity and unit",true),t("operator_name","Operator",true),
            d("amount","Amount (INR)",default="0"),Field("notes","Notes",Kind.LONG,maxLength=2000)
        ),summary=listOf("activity_type","quantity_metric","created_at")),
        Module("inventory","Inventory","Business","Inventory","item_name",listOf(
            t("item_name","Item name",true),t("category","Category",true),
            rel("farm","Farm","farms/"),rel("vendor","Vendor","vendors/"),
            d("quantity","Stock quantity",true,default="0"),t("unit","Unit",true),
            d("unit_price","Unit price (INR)",true,default="0"),
            Field("safety_stock","Low stock threshold",Kind.INTEGER,nonNegative=true,default="5"),
            t("location","Storage location",true),
            choice("status","Status","In Stock","Low Stock","Out of Stock","Operational","Maintenance","Unavailable",default="In Stock"),
            date("purchase_date","Purchase date"),t("license_plate","Vehicle registration"),
            t("fuel_type","Fuel type"),date("last_service_date","Last service"),
            date("next_service_date","Next service"),
            Field("rental_available","Available for rental",Kind.BOOLEAN,default="false"),
            d("rental_price","Rental price (INR)"),t("rental_unit","Rental period"),
            Field("description","Description",Kind.LONG,maxLength=2000)
        ),delete=true,summary=listOf("quantity","unit","unit_price","status")),
        Module("expenses","Expenses","Business","Expenses","description",listOf(
            t("description","Expense description",true),rel("farm","Farm","farms/"),
            rel("crop_split_id","Crop","crop-splits/",parent="farm",query="farm_id"),
            rel("vendor","Vendor","vendors/"),t("category","Category",true),
            date("close_date","Expense date",true),d("amount","Total expense (INR)",true,true),
            d("balance_amount","Balance due (INR)",default="0"),payment,
            d("quantity","Quantity"),t("unit","Unit"),t("tags","Notes")
        ),summary=listOf("close_date","category","amount","balance_due")),
        Module("transactions","Sales and ledger","Business","Sales","description",create=false,edit=false,
            summary=listOf("close_date","amount","paid_amount","balance_amount","status")),
        Module("customers","Customers","Business","Sales","name",listOf(
            t("name","Customer name",true),t("company_name","Company"),
            phone("contact_number","Mobile number"),Field("email","Email",Kind.EMAIL),
            t("state","State"),t("district","District"),Field("pincode","Pincode",maxLength=6),
            t("address","Address"),choice("status","Status","Active","Inactive",default="Active")
        ),summary=listOf("company_name","contact_number","email")),
        Module("vendors","Vendors","Business","Expenses","vendor_name",listOf(
            t("vendor_name","Vendor name",true),t("business_name","Business"),
            phone("mobile_number","Mobile number"),t("state","State"),t("district","District"),
            Field("pincode","Pincode",maxLength=6)
        ),summary=listOf("business_name","mobile_number")),
        Module("outstanding","Outstanding balances","Business","Expenses","invoice_no",create=false,edit=false,
            summary=listOf("customer_name","amount_total","amount_paid","balance_due","due_date","status")),
        Module("users","Employees and managers","Workforce","Employee","name",listOf(
            t("name","Full name",true),phone("mobile_number","Mobile number",true),
            date("date_of_birth","Date of birth"),date("joining_date","Joining date"),
            choice("gender","Gender","Male","Female","Other"),
            rel("designation","Designation","dropdowns/designations/"),
            t("role","Role",true),Field("is_role_manager","Manager login access",Kind.BOOLEAN,default="false"),
            Field("permissions","Allowed modules",Kind.MULTI,
                options=listOf("Dashboard","Farms","Market & Resources Hub","Task","Sales","Inventory","Expenses","Employee","Reports")),
            choice("status","Account status","0","1",default="0"),
            d("base_daily_rate","Daily rate (INR)",true,default="500"),
            t("salary_frequency","Salary frequency"),d("salary","Salary (INR)"),t("address","Address")
        ),summary=listOf("mobile_number","role","is_role_manager","status")),
        Module("attendance","Attendance","Workforce","Employee","staff_name",listOf(
            staff,date("date","Date",true),choice("status","Attendance","P","A","H","-",default="P")
        ),summary=listOf("staff","date","status","is_paid")),
        Module("advances","Staff advances","Workforce","Employee","staff_name",listOf(
            staff,d("amount","Advance amount (INR)",true,true),payment
        ),edit=false,summary=listOf("staff","date","amount","is_paid")),
        Module("payrolls","Payroll history","Workforce","Employee","staff_name",create=false,edit=false,
            summary=listOf("month","present_days","advance_deduction","net_pay","paid_on")),
        Module("market-prices","Market prices","Market and knowledge","Market & Resources Hub","crop_name",
            create=false,edit=false,summary=listOf("base_price","unit","trend_percentage","trend_direction")),
        Module("daily-price-history","Price history","Market and knowledge","Market & Resources Hub","crop_name",
            create=false,edit=false,summary=listOf("date","price","unit","depot_name")),
        Module("depots","Market depots","Market and knowledge","Market & Resources Hub","terminal_name",
            create=false,edit=false,summary=listOf("name","location","district","contact_number")),
        Module("locations","Nearby services","Market and knowledge","Market & Resources Hub","name",listOf(
            t("name","Service name",true),t("service_class","Service class",true),
            t("address","Address"),phone("contact_number","Mobile number"),d("distance_km","Distance (km)")
        ),edit=false,summary=listOf("service_class","address","contact_number")),
        Module("labour","On demand labour","Market and knowledge","Market & Resources Hub","name",listOf(
            t("name","Worker name",true),t("workforce_class","Workforce class",true),
            t("specialty_skill","Specialty skill",true),d("wage_rate","Wage rate (INR)",true,true),
            phone("contact_phone","Mobile number",true),
            choice("status","Availability","Available","Booked","On Leave",default="Available")
        ),summary=listOf("specialty_skill","wage_rate","contact_phone","status")),
        Module("equipment","Rental equipment","Market and knowledge","Market & Resources Hub","equipment_name",
            create=false,edit=false,summary=listOf("owner_name","price_per_day","rental_unit","contact_phone","status")),
        Module("blueprints","Crop blueprints","Market and knowledge","Farms","crop_name",listOf(
            t("crop_name","Crop name",true),t("cultivation_season","Cultivation season",true),
            Field("yield_cycle_days","Yield cycle (days)",Kind.INTEGER,positive=true,default="90")
        ),summary=listOf("cultivation_season","yield_cycle_days")),
        Module("blueprint-stages","Blueprint milestones","Market and knowledge","Farms","milestone_title",listOf(
            rel("blueprint","Blueprint","blueprints/",true),t("milestone_title","Milestone",true),
            t("activity_type","Activity type",true),
            Field("day_offset","Days after sowing",Kind.INTEGER,nonNegative=true,default="0"),
            choice("priority_level","Priority","Low","Medium","High",default="Medium")
        ),summary=listOf("day_offset","activity_type","priority_level")),
        Module("guidelines","Farming guidelines","Market and knowledge","Farms","title",create=false,edit=false,
            summary=listOf("category_name","author_name","read_time")),
        Module("documents","My documents","Account","Account","title",create=false,edit=false,
            summary=listOf("category","file_type","file_size_display","created_at")),
        Module("notifications","Notifications","Account","Account","title",create=false,edit=false,
            summary=listOf("message","created_at","is_read"))
    )
    val aliases = mapOf(
        "Farms" to setOf("Farms","Farms & Crop Splits"),
        "Task" to setOf("Task","Tasks","Task Management"),
        "Inventory" to setOf("Inventory","Resource Consumption"),
        "Sales" to setOf("Sales","Financials & Sales"),
        "Expenses" to setOf("Expenses","Expense","Outstanding","Outstanding Liabilities","Financials & Sales"),
        "Employee" to setOf("Employee","Employees","Employee Management","Employee Shifts"),
        "Reports" to setOf("Reports"), "Market & Resources Hub" to setOf("Market & Resources Hub")
    )
    fun allowed(permission: String, modules: Set<String>, manager: Boolean, grants: Set<String>): Boolean {
        if (permission == "Account") return true
        val names = aliases[permission] ?: setOf(permission)
        return names.any { it in modules } && (!manager || "ALL" in grants || names.any { it in grants })
    }
    fun module(key: String) = modules.first { it.key == key }
}
