package com.agriot.farmer

import android.app.Application
import androidx.compose.runtime.*
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.agriot.farmer.data.*
import com.agriot.farmer.domain.*
import java.math.BigDecimal
import java.time.LocalDate
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

sealed interface Screen {
    data object Home : Screen
    data class Records(val module: Module, val filter: String = "") : Screen
    data class Detail(val module: Module, val row: JSONObject) : Screen
    data class Edit(val module: Module, val row: JSONObject? = null) : Screen
    data class Read(val title: String, val path: String) : Screen
    data class Action(val title: String, val path: String, val fields: List<Field>,
        val type: String, val row: JSONObject? = null) : Screen
}
class AppModel(app: Application) : AndroidViewModel(app) {
    val api = FarmerApi(DeviceVault(app))
    var signedIn by mutableStateOf(api.signedIn); private set
    var screen by mutableStateOf<Screen>(Screen.Home); private set
    var busy by mutableStateOf(false); private set
    var error by mutableStateOf<String?>(null); private set
    var message by mutableStateOf<String?>(null); private set
    var uncertain by mutableStateOf(false); private set
    var otpSent by mutableStateOf(false); private set
    var registration by mutableStateOf(false); private set
    var otpResendAt by mutableLongStateOf(0L); private set
    var rows by mutableStateOf<List<JSONObject>>(emptyList()); private set
    var next by mutableStateOf<String?>(null); private set
    var cachedAt by mutableStateOf<Long?>(null); private set
    var readValue by mutableStateOf<Any>(JSONObject()); private set
    var overview by mutableStateOf(JSONObject()); private set
    var profile by mutableStateOf(JSONObject()); private set
    var subscription by mutableStateOf(JSONObject()); private set
    var moduleNames by mutableStateOf<Set<String>>(emptySet()); private set
    var grants by mutableStateOf<Set<String>>(emptySet()); private set
    var accessOnline by mutableStateOf(false); private set
    var isManager by mutableStateOf(api.managerSession()); private set
    var accessNotice by mutableStateOf<String?>(null); private set
    val visibleModules get() = Catalog.modules.filter { allowed(it.permission) }
    fun allowed(permission: String) = Catalog.allowed(permission,moduleNames,isManager,grants)
    val writable get() = accessOnline && cachedAt == null && !uncertain
    init { if (signedIn) home() }
    fun dismissError() { error=null }
    fun dismissMessage() { message=null }
    private fun job(block: suspend () -> Unit) {
        if (busy) return
        busy=true;error=null;message=null
        viewModelScope.launch {
            try { block() }
            catch(e: CancellationException) { throw e }
            catch(e: Exception) {
                error=e.message ?: "This action could not be completed"
                uncertain=(e as? ApiFailure)?.uncertain == true
                if (!api.signedIn && signedIn) clearSession()
            } finally { busy=false }
        }
    }
    private fun clearSession() {
        signedIn=false;otpSent=false;screen=Screen.Home;rows=emptyList();overview=JSONObject()
        profile=JSONObject();subscription=JSONObject();moduleNames=emptySet();grants=emptySet()
        readValue=JSONObject();accessOnline=false;isManager=false;cachedAt=null
    }
    fun changeLoginMode(register: Boolean) { if(!busy) { registration=register;otpSent=false;error=null } }
    fun sendOtp(mobile: String, details: Map<String,String> = emptyMap()) = job {
        require(Validation.phone(mobile)) { "Enter a valid 10-digit Indian mobile number" }
        require(System.currentTimeMillis() >= otpResendAt) { "Wait before requesting another code" }
        val payload=details + ("mobile" to mobile)
        api.form(if(registration) "register/" else "login/send-otp/",payload)
        otpSent=true;otpResendAt=System.currentTimeMillis()+60_000
        message="Code sent. Enter the six digits received on your phone."
    }
    fun verifyOtp(mobile: String, otp: String) = job {
        require(Regex("^[0-9]{6}$").matches(otp)) { "Enter the six-digit code" }
        val result=api.form(if(registration) "verify_otp_and_payment/" else "login/verify-otp/",
            mapOf("mobile" to mobile,"otp" to otp)) as JSONObject
        api.acceptToken(result.getString("token"));signedIn=true;otpSent=false
        screen=Screen.Home;loadHome()
    }
    fun logout() = job {
        try { api.write("logout/",JSONObject()) } catch (_: Exception) { /* Device session is always removed. */ }
        api.signOut();clearSession();uncertain=false
    }
    private suspend fun loadAccess() {
        accessOnline=false;moduleNames=emptySet();grants=emptySet();accessNotice=null
        isManager=api.managerSession()
        val perm=api.read("manager-permissions/",false).value as JSONObject
        isManager=perm.optInt("is_manager")==1
        grants=strings(perm.optJSONArray("permissions")).toSet()
        profile=api.read("profile/",false).value as JSONObject
        try {
            subscription=api.read("subscription/",false).value as JSONObject
            val active=subscription.optString("subscription_status").equals("active",true)
            val end=subscription.optString("end_date")
            if (active && (end.isBlank() || end >= LocalDate.now().toString())) {
                moduleNames=strings(subscription.optJSONArray("modules")).toSet()
                accessOnline=true
            } else accessNotice="Your subscription is inactive or has expired. Review your package to restore access."
        } catch(e: ApiFailure) {
            if(e.status!=404) throw e
            subscription=JSONObject()
            accessNotice="No subscription is available for this account. Review the available packages."
        }
    }
    private suspend fun loadHome() {
        rows=emptyList();next=null;cachedAt=null;overview=JSONObject();uncertain=false
        loadAccess()
        // Current dashboard aggregates are owner-wide. Do not show them to a manager.
        if (!isManager && accessOnline) {
            val loaded=api.read("dashboard/overview/")
            overview=loaded.value as JSONObject;cachedAt=loaded.cachedAt
        }
    }
    fun home() = job { screen=Screen.Home;loadHome() }
    fun records(module: Module,filter: String="") = job {
        require(allowed(module.permission)) { "This module is not enabled for your account" }
        screen=Screen.Records(module,filter);rows=emptyList();next=null;cachedAt=null;uncertain=false
        loadRecords(module.path+filter)
    }
    private suspend fun loadRecords(path: String,append: Boolean=false) {
        val loaded=api.read(path)
        val page=FarmerApi.page(loaded.value)
        rows=if(append) (rows+page.rows).distinctBy { it.optString("id")+it.optString("is_farmer") } else page.rows
        next=page.next;cachedAt=loaded.cachedAt
    }
    fun more() = job { next?.let { loadRecords(it,true) } }
    fun refresh() {
        when(val current=screen) {
            Screen.Home -> home()
            is Screen.Records -> records(current.module,current.filter)
            is Screen.Detail -> detail(current.module,current.row)
            is Screen.Read -> read(current.title,current.path)
            is Screen.Edit -> records(current.module)
            is Screen.Action -> home()
        }
    }
    fun detail(module: Module,row: JSONObject) = job {
        if(!module.edit) { screen=Screen.Detail(module,row);return@job }
        val loaded=api.read(module.path+row.get("id")+"/")
        screen=Screen.Detail(module,loaded.value as JSONObject);cachedAt=loaded.cachedAt
    }
    fun edit(module: Module,row: JSONObject?=null) = job {
        check(writable) { "Connect and refresh your account before making changes" }
        require(allowed(module.permission))
        val fresh=if(row!=null) api.read(module.path+row.get("id")+"/",false).value as JSONObject else null
        screen=Screen.Edit(module,fresh);cachedAt=null
    }
    fun back() {
        if(busy) return
        error=null;uncertain=false
        when(val current=screen) {
            is Screen.Detail -> records(current.module)
            is Screen.Edit -> if(current.row!=null) detail(current.module,current.row) else records(current.module)
            is Screen.Action -> home()
            else -> home()
        }
    }
    fun read(title: String,path: String) = job {
        screen=Screen.Read(title,path);readValue=JSONObject();cachedAt=null
        val loaded=api.read(path)
        readValue=loaded.value;cachedAt=loaded.cachedAt
    }
    fun action(value: Screen.Action) { if(!busy && writable) { error=null;screen=value } }
    private suspend fun checkLimits(module: Module, row: JSONObject?, body: JSONObject) {
        if(module.key=="farms" && body.has("total_area")) {
            val acreage=BigDecimal(body.getString("total_area"))
            val old=if(row!=null) api.read(module.path+row.get("id")+"/",false).value as JSONObject else JSONObject()
            require(acreage >= BigDecimal(old.optString("allocated_acres","0"))) { "Area cannot be smaller than the allocated crop area" }
            val sub=api.read("subscription/",false).value as JSONObject
            val remaining=Validation.limit(sub.optString("balance_acre_limit"))
            if(remaining!=null) require(acreage <= remaining+BigDecimal(old.optString("total_area","0"))) { "This farm exceeds the package acreage balance" }
            if(row==null) Validation.limit(sub.optString("balance_land_limit"))?.let { require(it>BigDecimal.ZERO) { "Your package farm limit has been reached" } }
        }
        if(module.key=="crop-splits") {
            val farmId=body.optString("farm",row?.optString("farm").orEmpty())
            val farm=api.read("farms/"+farmId+"/",false).value as JSONObject
            val oldArea=if(row!=null && row.optString("farm")==farmId) BigDecimal(row.optString("acreage","0")) else BigDecimal.ZERO
            val area=body.optString("acreage",row?.optString("acreage","0") ?: "0").toBigDecimal()
            val available=BigDecimal(farm.getString("total_area"))-BigDecimal(farm.optString("allocated_acres","0"))+oldArea
            require(area<=available) { "Crop acreage exceeds the farm's unallocated area" }
        }
    }
    fun save(module: Module,row: JSONObject?,values: Map<String,String>) = job {
        require(writable && allowed(module.permission)) { "Refresh online before saving" }
        require(Validation.errors(module.fields,values).isEmpty()) { "Correct the highlighted fields" }
        val original=if(row==null) null else Validation.initial(module.fields,row)
        val body=Validation.payload(module.fields,values,original)
        if(body.length()==0) { message="No changes to save";return@job }
        checkLimits(module,row,body)
        if(module.key=="expenses") {
            body.optString("amount").toBigDecimalOrNull()?.let { body.put("amount",it.abs().negate().toPlainString()) }
            if(row==null) body.put("status","Cost")
        }
        if(module.key=="consumption") {
            val item=api.read("inventory/"+values.getValue("inventory_item")+"/",false).value as JSONObject
            val quantity=values.getValue("quantity").toBigDecimal()
            require(quantity<=BigDecimal(item.optString("quantity","0"))) { "Not enough stock for this consumption" }
            body.put("asset_name",item.getString("item_name"))
                .put("quantity_metric",quantity.toPlainString()+" "+item.optString("unit"))
                .put("activity_type","Resource Consumption")
        }
        if(module.key=="users" && body.has("permissions")) {
            val requested=strings(body.optJSONArray("permissions"))
            require(requested.all { it=="Dashboard" || Catalog.allowed(it,moduleNames,false,emptySet()) }) {
                "Manager permissions must be within the subscribed modules"
            }
        }
        api.write(module.path+(row?.let{it.get("id").toString()+"/"} ?: ""),body,if(row==null) "POST" else "PATCH")
        screen=Screen.Records(module);loadRecords(module.path);message="Saved successfully"
    }
    fun remove(module: Module,row: JSONObject) = job {
        require(writable && module.delete && allowed(module.permission))
        val fresh=api.read(module.path+row.get("id")+"/",false).value as JSONObject
        require(fresh.optBoolean("can_delete",true)) { fresh.optString("delete_block_reason","This record has linked data") }
        api.write(module.path+row.get("id")+"/",JSONObject(),"DELETE")
        screen=Screen.Records(module);loadRecords(module.path);message="Deleted successfully"
    }
    suspend fun lookup(field: Field,values: Map<String,String>): List<Pair<String,String>> {
        if(field.options.isNotEmpty()) return field.options.map { it to it }
        var path=field.source
        if(field.parent.isNotBlank()) {
            val parent=values[field.parent].orEmpty()
            if(parent.isBlank()) return emptyList()
            path+="?"+field.parentQuery+"="+parent
        }
        val result=mutableListOf<JSONObject>()
        var page: String?=path
        var count=0
        while(page!=null && count<100) {
            val batch=FarmerApi.page(api.read(page,false).value)
            result.addAll(batch.rows);page=batch.next;count++
        }
        require(page==null) { "This lookup is too large. Refine the parent selection." }
        return result.filter { field.key!="supervisors" || (it.optBoolean("is_role_manager") && it.optString("status")=="0") }
            .map { row ->
                row.get("id").toString() to listOf("land_name","name","crop_name","item_name","vendor_name","title","milestone_title")
                    .firstNotNullOfOrNull { row.optString(it).takeIf { it.isNotBlank() && it!="null" } }.orEmpty()
            }
    }
    fun submitAction(action: Screen.Action,values: Map<String,String>) = job {
        require(writable) { "Refresh online before saving" }
        require(Validation.errors(action.fields,values).isEmpty()) { "Correct the highlighted fields" }
        val body=Validation.payload(action.fields,values)
        when(action.type) {
            "switch" -> {
                val result=api.write(action.path,body) as JSONObject
                api.acceptToken(result.getString("token"))
                screen=Screen.Home;loadHome();message="Profile switched";return@job
            }
            "settle" -> {
                val row=api.read("outstanding/"+action.row!!.get("id")+"/",false).value as JSONObject
                val remaining=BigDecimal(row.getString("amount_total"))-BigDecimal(row.getString("amount_paid"))
                val paid=values.getValue("amount_paid").toBigDecimal()
                require(paid>BigDecimal.ZERO && paid<=remaining) { "Payment must be positive and cannot exceed the current balance" }
                body.put("payment_type",if(paid==remaining) "full" else "partial")
            }
            "harvest" -> {
                val qty=values.getValue("yield_tons").toBigDecimal()
                val rate=values["price_per_mt"].orEmpty().toBigDecimalOrNull() ?: BigDecimal.ZERO
                body.put("total_sale_value",(qty*rate).toPlainString())
                body.put("batches_json",JSONArray().put(JSONObject().put("yield",qty.toPlainString())
                    .put("price",rate.toPlainString()).put("saleValue",(qty*rate).toPlainString())
                    .put("quality",values["quality"] ?: "Grade 1").put("unit",values["unit"] ?: "Kg")
                    .put("status","Ready").put("flush_no",action.row!!.optInt("current_flush",1))))
            }
            "sale" -> {
                val qty=values.getValue("load_weight").toBigDecimal()
                val rate=values.getValue("unit_rate").toBigDecimal()
                val subtotal=qty*rate
                val net=Validation.saleTotal(subtotal,values.getValue("gst_rate").toBigDecimal(),
                    values.getValue("discount").toBigDecimal(),values.getValue("commission").toBigDecimal())
                val paid=values.getValue("paid_amount").toBigDecimal()
                require(net>BigDecimal.ZERO && paid<=net) { "The total must be positive; paid amount cannot exceed the total" }
                val customer=api.read("customers/"+values.getValue("customer_id")+"/",false).value as JSONObject
                body.put("description",customer.getString("name")).put("amount",subtotal.toPlainString())
                    .put("discount_type","amount").put("commission_type","amount").remove("unit_rate")
                body.remove("customer_id")
                action.row?.let { row ->
                    body.put("crop_split_id",row.get("id")).put("farm_id",row.get("farm"))
                        .put("category",row.optString("crop_name"))
                }
            }
        }
        api.write(action.path,body)
        when(action.type) {
            "harvest","advance" -> {
                val row=api.read("crop-splits/"+action.row!!.get("id")+"/",false).value as JSONObject
                screen=Screen.Detail(Catalog.module("crop-splits"),row)
            }
            "settle" -> { screen=Screen.Records(Catalog.module("outstanding"));loadRecords("outstanding/") }
            "sale" -> { screen=Screen.Records(Catalog.module("transactions"));loadRecords("transactions/") }
            else -> { screen=Screen.Home;loadHome() }
        }
        message="Saved successfully"
    }
    fun uploadDocument(values: Map<String,String>,name: String,mime: String,bytes: ByteArray) = job {
        require(writable && values["title"].orEmpty().isNotBlank()) { "Enter a document title and connect before uploading" }
        api.upload("documents/",values,name,mime,bytes)
        screen=Screen.Records(Catalog.module("documents"));loadRecords("documents/");message="Document uploaded"
    }
    fun markNotification(row: JSONObject) = job {
        api.write("notifications/"+row.get("id")+"/read/",JSONObject())
        screen=Screen.Records(Catalog.module("notifications"));loadRecords("notifications/")
    }
    companion object {
        fun strings(array: JSONArray?): List<String> = if(array==null) emptyList() else (0 until array.length()).map{array.get(it).toString()}
    }
}
