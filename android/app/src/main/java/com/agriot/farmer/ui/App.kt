@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
package com.agriot.farmer.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Intent
import android.net.Uri
import android.text.Html
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.agriot.farmer.*
import com.agriot.farmer.data.FarmerApi
import com.agriot.farmer.domain.*
import java.time.LocalDate
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

private val green = Color(0xFF204C35)
private val cream = Color(0xFFF5F7EF)
private val colors = lightColorScheme(primary=green,onPrimary=Color.White,secondary=Color(0xFF586C3A),
    background=cream,surface=Color(0xFFFFFEFA),surfaceVariant=Color(0xFFE7EDDF))
@Composable fun AgriOTApp(model: AppModel) {
    MaterialTheme(colorScheme=colors) {
        Surface(Modifier.fillMaxSize()) {
            if (!model.signedIn) Login(model) else FarmerShell(model)
        }
    }
}
@Composable private fun FarmerShell(model: AppModel) {
    val screen=model.screen
    BackHandler(screen != Screen.Home) { model.back() }
    val title=when(screen) {
        Screen.Home -> "Your farm, in focus"
        is Screen.Records -> screen.module.title
        is Screen.Detail -> screen.module.recordTitle(screen.row)
        is Screen.Edit -> (if(screen.row==null) "Add " else "Edit ") + screen.module.title.lowercase()
        is Screen.Read -> screen.title
        is Screen.Action -> screen.title
    }
    Scaffold(
        topBar={
            TopAppBar(title={Column { Text("AgriOT",fontWeight=FontWeight.Bold,color=green)
                Text("FARMER WORKSPACE · REVIEW BUILD",style=MaterialTheme.typography.labelSmall) }},
                navigationIcon={if(screen!=Screen.Home) IconButton(onClick={model.back()},enabled=!model.busy) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack,"Back") }},
                actions={
                    IconButton(onClick={model.refresh()},enabled=!model.busy && screen !is Screen.Edit && screen !is Screen.Action) { Icon(Icons.Default.Refresh,"Refresh") }
                    IconButton(onClick={model.logout()},enabled=!model.busy) { Icon(Icons.Default.Logout,"Sign out") }
                })
        },
        bottomBar={if(screen==Screen.Home) Surface(tonalElevation=2.dp) {
            Row(Modifier.fillMaxWidth().navigationBarsPadding(),horizontalArrangement=Arrangement.SpaceEvenly) {
                TextButton(onClick={model.read("My profile","profile/")},enabled=!model.busy) { Text("Profile") }
                TextButton(onClick={model.read("Subscription","subscription/")},enabled=!model.busy) { Text("Package") }
                TextButton(onClick={model.records(Catalog.module("notifications"))},enabled=!model.busy) { Text("Inbox") }
            }
        }}
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            if(model.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            Text(title,Modifier.padding(horizontal=20.dp,vertical=12.dp),style=MaterialTheme.typography.headlineSmall,fontWeight=FontWeight.SemiBold)
            model.cachedAt?.let { at -> Notice("Saved copy · "+java.text.DateFormat.getDateTimeInstance().format(java.util.Date(at))+
                "\nConnect and refresh before making changes.") }
            model.error?.let { ErrorNotice(it,model.uncertain) }
            model.message?.let { Notice(it) }
            when(screen) {
                Screen.Home -> Home(model)
                is Screen.Records -> Records(model,screen)
                is Screen.Detail -> Detail(model,screen)
                is Screen.Edit -> Editor(model,screen)
                is Screen.Read -> ReadScreen(model,screen)
                is Screen.Action -> ActionEditor(model,screen)
            }
        }
    }
}
@Composable private fun Notice(text: String) {
    Surface(color=MaterialTheme.colorScheme.surfaceVariant,shape=MaterialTheme.shapes.medium,
        modifier=Modifier.fillMaxWidth().padding(horizontal=20.dp,vertical=4.dp)) {
        Text(text,Modifier.padding(12.dp),style=MaterialTheme.typography.bodyMedium)
    }
}
@Composable private fun ErrorNotice(text: String,uncertain: Boolean=false) {
    Surface(color=MaterialTheme.colorScheme.errorContainer,shape=MaterialTheme.shapes.medium,
        modifier=Modifier.fillMaxWidth().padding(horizontal=20.dp,vertical=4.dp)) {
        Column(Modifier.padding(12.dp)) {
            Text(text,color=MaterialTheme.colorScheme.onErrorContainer)
            if(uncertain) Text("Use Back and refresh the list to check the result before submitting again.",fontWeight=FontWeight.Bold)
        }
    }
}
@Composable private fun Home(model: AppModel) {
    LazyColumn(contentPadding=PaddingValues(20.dp),verticalArrangement=Arrangement.spacedBy(14.dp)) {
        item {
            Text("Welcome, "+model.profile.optString("name",
                model.profile.optString("farmer_name","farmer")),style=MaterialTheme.typography.titleLarge)
            Text(if(model.isManager) "Manager workspace" else "Manage your land, people and daily work.",
                style=MaterialTheme.typography.bodyMedium)
        }
        model.accessNotice?.let { item { Notice(it) } }
        if(!model.isManager && model.overview.length()>0) {
            val cards=listOf("total_farms" to "Farms","active_crops" to "Active crops","pending_tasks" to "Pending tasks",
                "inventory_value" to "Inventory value (INR)","receivables" to "Receivables (INR)","payables" to "Payables (INR)")
            items(cards.chunked(2)) { pair ->
                Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                    pair.forEach { (key,label) ->
                        Card(Modifier.weight(1f)) { Column(Modifier.padding(16.dp)) {
                            Text(label,style=MaterialTheme.typography.labelMedium)
                            Text(Validation.display(model.overview.opt(key)),style=MaterialTheme.typography.headlineSmall,fontWeight=FontWeight.Bold)
                        }}
                    }
                }
            }
        }
        model.visibleModules.groupBy { it.group }.forEach { (group,modules) ->
            item { Text(group,style=MaterialTheme.typography.titleMedium,color=green,fontWeight=FontWeight.Bold) }
            items(modules.chunked(2)) { pair ->
                Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                    pair.forEach { module ->
                        OutlinedCard(onClick={if(!model.busy) model.records(module)},modifier=Modifier.weight(1f)) {
                            Column(Modifier.padding(16.dp).heightIn(min=65.dp),verticalArrangement=Arrangement.Center) {
                                Icon(iconFor(module.key),null,tint=green,modifier=Modifier.size(24.dp))
                                Spacer(Modifier.height(8.dp))
                                Text(module.title,style=MaterialTheme.typography.titleSmall)
                            }
                        }
                    }
                    if(pair.size==1) Spacer(Modifier.weight(1f))
                }
            }
        }
        if(model.allowed("Reports")) item {
            Text("Reports",style=MaterialTheme.typography.titleMedium,color=green)
            FlowRow(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick={model.read("Sales summary","sales/summary/")},enabled=!model.busy) { Text("Sales") }
                OutlinedButton(onClick={model.read("Expense summary","expenses/summary/")},enabled=!model.busy) { Text("Expenses") }
                OutlinedButton(onClick={model.read("Yield pipeline","yield/summary/")},enabled=!model.busy) { Text("Yield") }
                OutlinedButton(onClick={model.read("Payroll run sheet","payrolls/run_sheet/")},enabled=!model.busy) { Text("Payroll") }
            }
        }
        item { OutlinedButton(onClick={model.read("Available profiles","available-profiles/")},enabled=!model.busy) { Text("Switch farmer / manager profile") } }
    }
}
private fun iconFor(key: String) = when(key) {
    "farms","crop-splits","blueprints" -> Icons.Default.Grass
    "tasks","attendance" -> Icons.Default.Event
    "inventory","equipment" -> Icons.Default.Inventory2
    "users","labour" -> Icons.Default.Groups
    "expenses","transactions","outstanding","payrolls","advances" -> Icons.Default.AccountBalanceWallet
    "market-prices","daily-price-history" -> Icons.Default.TrendingUp
    "documents" -> Icons.Default.Description
    "notifications" -> Icons.Default.Notifications
    else -> Icons.Default.GridView
}
@Composable private fun Records(model: AppModel,screen: Screen.Records) {
    var query by rememberSaveable(screen.module.key,screen.filter) { mutableStateOf("") }
    val module=screen.module
    var upload by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().padding(horizontal=20.dp)) {
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween,verticalAlignment=Alignment.CenterVertically) {
            Text(model.rows.size.toString()+" records loaded",style=MaterialTheme.typography.labelMedium)
            if(module.create) FilledTonalButton(onClick={model.edit(module)},enabled=!model.busy&&model.writable) { Text("Add") }
            if(module.key=="documents") FilledTonalButton(onClick={upload=true},enabled=!model.busy&&model.writable) { Text("Upload") }
        }
        if(module.key=="tasks") Row {
            val context=LocalContext.current
            TextButton(onClick={
                val today=LocalDate.now()
                DatePickerDialog(context,{_,y,m,d->
                    val day=LocalDate.of(y,m+1,d).toString()
                    model.records(module,"?date_from="+day+"&date_to="+day)
                },today.year,today.monthValue-1,today.dayOfMonth).show()
            },enabled=!model.busy) { Text("Choose task date") }
            if(screen.filter.isNotBlank()) TextButton(onClick={model.records(module)},enabled=!model.busy) { Text("All dates") }
        }
        OutlinedTextField(query,{query=it},Modifier.fillMaxWidth().padding(vertical=8.dp),
            label={Text("Search loaded records")},singleLine=true,leadingIcon={Icon(Icons.Default.Search,null)})
        LazyColumn(verticalArrangement=Arrangement.spacedBy(10.dp),contentPadding=PaddingValues(bottom=20.dp)) {
            val visible=model.rows.filter { query.isBlank() || it.toString().contains(query,true) }
                .let { if(module.key=="tasks") it.sortedBy { row -> row.optString("task_date") } else it }
            if(visible.isEmpty()&&!model.busy) item { Text(if(model.error==null) "No records to show." else "Refresh to try loading this list again.",Modifier.padding(vertical=24.dp)) }
            items(visible) { row ->
                OutlinedCard(onClick={if(!model.busy)model.detail(module,row)},modifier=Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp),verticalArrangement=Arrangement.spacedBy(6.dp)) {
                        Text(module.recordTitle(row),style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.SemiBold)
                        module.summary.forEach { key ->
                            val value=Validation.display(row.opt(key))
                            if(value.isNotBlank()) Text(label(key)+": "+value,style=MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
            }
            if(model.next!=null) item { OutlinedButton(onClick={model.more()},enabled=!model.busy,modifier=Modifier.fillMaxWidth()) { Text("Load more records") } }
        }
    }
    if(upload) DocumentUpload(model) { upload=false }
}
@Composable private fun Detail(model: AppModel,screen: Screen.Detail) {
    val context=LocalContext.current
    val module=screen.module;val row=screen.row
    var deleteConfirm by remember { mutableStateOf(false) }
    LazyColumn(contentPadding=PaddingValues(20.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
        item { FlowRow(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            if(module.edit) Button(onClick={model.edit(module,row)},enabled=!model.busy&&model.writable) { Text("Edit") }
            if(module.delete) OutlinedButton(onClick={deleteConfirm=true},enabled=!model.busy&&model.writable&&row.optBoolean("can_delete",true)) { Text("Delete") }
            if(module.key=="farms") {
                OutlinedButton(onClick={model.records(Catalog.module("crop-splits"),"?farm_id="+row.optString("id"))},enabled=!model.busy) { Text("View crops") }
                OutlinedButton(onClick={model.records(Catalog.module("tasks"),"?farm_id="+row.optString("id"))},enabled=!model.busy) { Text("View tasks") }
            }
            if(module.key=="crop-splits") {
                val stage=row.optString("stage")
                if(stage in listOf("Sown","Vegetative","Flowering"))
                    Button(onClick={model.action(Screen.Action("Advance growth stage","crop-splits/"+row.get("id")+"/advance/",emptyList(),"advance",row))},
                        enabled=model.writable&&!model.busy) { Text("Advance stage") }
                if(stage=="Yield") Button(onClick={model.action(harvestAction(row))},enabled=model.writable&&!model.busy) { Text("Record harvest") }
                if(stage in listOf("Harvested","Partially Sold") && row.optString("yield_tons","0").toBigDecimalOrNull()?.signum()==1) Button(onClick={model.action(saleAction(row))},enabled=model.writable&&!model.busy&&model.allowed("Sales")) { Text("Sell harvested crop") }
                val multiple=row.optString("harvest_type").lowercase() in setOf("multiple","multiple harvest")
                if(multiple && stage in listOf("Harvested","Partially Sold","Sold") &&
                    row.optInt("current_flush",1)<row.optInt("total_flushes",1)) {
                    OutlinedButton(onClick={model.action(Screen.Action("Start next harvest flush",
                        "crop-splits/"+row.get("id")+"/advance/",
                        emptyList(),
                        "next-flush",row))},enabled=model.writable&&!model.busy) { Text("Start next flush") }
                }
                OutlinedButton(onClick={model.read("Crop overview","crop-splits/"+row.get("id")+"/profile/")},enabled=!model.busy) { Text("Crop overview") }
            }
            if(module.key=="outstanding" && row.optString("status")!="Paid") {
                // Vendor settlement has a separate backend action and must never use customer pay.
                val vendor=row.optString("invoice_no").startsWith("Vendor:")
                if(!vendor) Button(onClick={model.action(settleAction(row))},enabled=model.writable&&!model.busy) { Text("Record payment") }
                else Text("Vendor payment recording is unavailable in this review build.",style=MaterialTheme.typography.bodySmall)
            }
            if(module.key=="notifications") OutlinedButton(onClick={model.markNotification(row)},enabled=model.writable&&!model.busy) { Text("Mark read") }
            if(module.key=="documents") row.optString("file_url").takeIf{it.startsWith("https://")}?.let { url ->
                OutlinedButton(onClick={openLink(context,url)},enabled=!model.busy) { Text("Open document") }
            }
            val lat=row.optString("latitude").toDoubleOrNull();val lon=row.optString("longitude").toDoubleOrNull()
            if(lat!=null&&lon!=null&&lat in -90.0..90.0&&lon in -180.0..180.0)
                OutlinedButton(onClick={openLink(context,"geo:"+lat+","+lon+"?q="+lat+","+lon)}) { Text("Open map") }
            val phone=listOf("contact_phone","contact_number","mobile_number").firstNotNullOfOrNull {
                row.optString(it).takeIf{p->Regex("^[+0-9 ]{8,16}$").matches(p)}
            }
            if(phone!=null) OutlinedButton(onClick={openLink(context,"tel:"+phone)}) { Text("Call") }
        }}
        if(!row.optBoolean("can_delete",true)) item { Text(row.optString("delete_block_reason"),style=MaterialTheme.typography.bodySmall) }
        item { DataBlock(row) }
    }
    if(deleteConfirm) AlertDialog(onDismissRequest={deleteConfirm=false},title={Text("Delete this record?")},
        text={Text("Delete "+module.recordTitle(row)+"? This removes the record from the shared farm account.")},
        confirmButton={TextButton(onClick={deleteConfirm=false;model.remove(module,row)}) { Text("Delete") }},
        dismissButton={TextButton(onClick={deleteConfirm=false}) { Text("Cancel") }})
}
private fun label(key: String) = key.replace('_',' ').replaceFirstChar { it.uppercase() }
private val hiddenKeys=setOf("farmer","user","token","otp","fcm_token","uuid_token","file","geo_marks",
    "category_color","can_delete","delete_block_reason","password")
@Composable private fun DataBlock(value: Any,depth: Int=0) {
    when(value) {
        is JSONObject -> Column(verticalArrangement=Arrangement.spacedBy(12.dp)) {
            value.keys().asSequence().filter{it !in hiddenKeys}.forEach { key ->
                val v=value.opt(key)
                if(v!=null&&v!=JSONObject.NULL&&v.toString().isNotBlank()) {
                    if(v is JSONObject || v is JSONArray) {
                        Text(label(key),fontWeight=FontWeight.SemiBold,color=green)
                        if(depth<3) DataBlock(v,depth+1)
                    } else {
                        Column { Text(label(key),style=MaterialTheme.typography.labelMedium,color=green)
                            Text(if(key=="detailed_content") Html.fromHtml(Validation.display(v),Html.FROM_HTML_MODE_LEGACY).toString() else Validation.display(v)) }
                    }
                }
            }
        }
        is JSONArray -> Column(verticalArrangement=Arrangement.spacedBy(10.dp)) {
            (0 until value.length()).forEach { i -> OutlinedCard(Modifier.fillMaxWidth()) {
                Box(Modifier.padding(12.dp)) { DataBlock(value.get(i),depth+1) }
            }}
        }
        else -> Text(Validation.display(value))
    }
}
@Composable private fun ReadScreen(model: AppModel,screen: Screen.Read) {
    Column(Modifier.padding(horizontal=20.dp).verticalScroll(rememberScrollState()),
        verticalArrangement=Arrangement.spacedBy(12.dp)) {
        if(screen.path=="subscription/") {
            OutlinedButton(onClick={model.read("Available packages","packages/")},enabled=!model.busy) { Text("Compare packages") }
            Text("Plan purchases require the verified payment integration before release.",style=MaterialTheme.typography.bodySmall)
        }
        if(screen.path=="available-profiles/") {
            val obj=model.readValue as? JSONObject
            val arr=obj?.optJSONArray("profiles") ?: JSONArray()
            (0 until arr.length()).forEach { i ->
                val profile=arr.getJSONObject(i);val type=profile.optString("profile_type")
                OutlinedButton(onClick={model.action(Screen.Action("Switch to "+type,"switch-profile/",
                    listOf(Field("profile_type","Profile",Kind.CHOICE,true,options=listOf(type),default=type)),"switch",profile))},
                    enabled=!model.busy&&model.writable&&!profile.optBoolean("is_active")) {
                    Text(profile.optString("name")+" · "+type)
                }
            }
        } else DataBlock(model.readValue)
        Spacer(Modifier.height(24.dp))
    }
}
private fun openLink(context: android.content.Context,url: String) {
    try {
        val uri=Uri.parse(url)
        require(uri.scheme in setOf("https","geo","tel"))
        context.startActivity(Intent(if(uri.scheme=="tel") Intent.ACTION_DIAL else Intent.ACTION_VIEW,uri))
    } catch (_: Exception) { Toast.makeText(context,"No application is available to open this item.",Toast.LENGTH_LONG).show() }
}
@Composable private fun Editor(model: AppModel,screen: Screen.Edit) {
    val initial=remember(screen.module.key,screen.row) { Validation.initial(screen.module.fields,screen.row) }
    EntryForm(model,screen.module.fields,initial,"Save changes",screen.module.key+screen.row?.optString("id").orEmpty()) {
        model.save(screen.module,screen.row,it)
    }
}
@Composable private fun ActionEditor(model: AppModel,screen: Screen.Action) {
    if(screen.type=="next-flush") Notice("Confirm to start the next harvest flush at the Sown stage.")
    if(screen.type=="advance") Notice("Current stage: "+screen.row?.optString("stage")+". Confirm to move to the next growth stage.")
    if(screen.type=="settle" || screen.type=="vendor-settle") Notice("This records a payment in the farm accounts. Review the invoice and amount before confirming.")
    EntryForm(model,screen.fields,Validation.initial(screen.fields,null),"Review and confirm",screen.path) { model.submitAction(screen,it) }
}
@Composable private fun EntryForm(model: AppModel,fields: List<Field>,initial: Map<String,String>,button: String,
    formKey: String,onSave: (Map<String,String>)->Unit) {
    var values by rememberSaveable(formKey) { mutableStateOf(initial) }
    var attempted by rememberSaveable(formKey) { mutableStateOf(false) }
    var confirm by remember { mutableStateOf(false) }
    val errors=Validation.errors(fields,values)
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal=20.dp),
        verticalArrangement=Arrangement.spacedBy(12.dp)) {
        fields.forEach { field ->
            FormField(model,field,values,if(attempted)errors[field.key] else null,enabled=!model.busy) { newValue ->
                val changed=values.toMutableMap().apply { put(field.key,newValue) }
                // Clear children only on a deliberate user change, never during async option loading.
                fun clearChildren(parent: String) {
                    fields.filter{it.parent==parent}.forEach { child -> changed[child.key]="";clearChildren(child.key) }
                }
                if(values[field.key]!=newValue) {
                    clearChildren(field.key)
                    if(field.key=="harvest_type") {
                        if(newValue=="single") changed["total_flushes"]="1"
                        else if(changed["total_flushes"]=="1") changed["total_flushes"]="3"
                    }
                }
                values=changed
            }
        }
        Button(onClick={attempted=true;if(errors.isEmpty())confirm=true},
            enabled=!model.busy&&model.writable,modifier=Modifier.fillMaxWidth().height(52.dp)) { Text(button) }
        if(!model.writable) Text("Refresh online before saving.",color=MaterialTheme.colorScheme.error)
        Spacer(Modifier.height(24.dp))
    }
    if(confirm) AlertDialog(onDismissRequest={confirm=false},title={Text("Save to your farm account?")},
        text={Text("These changes will be available to your farm team. Check the entered details before saving.")},
        confirmButton={TextButton(onClick={confirm=false;onSave(values)}) { Text("Confirm save") }},
        dismissButton={TextButton(onClick={confirm=false}) { Text("Go back") }})
}
@Composable private fun FormField(model: AppModel,field: Field,values: Map<String,String>,error: String?,
    enabled: Boolean,onValue: (String)->Unit) {
    val context=LocalContext.current;val value=values[field.key].orEmpty()
    val title=field.label+(if(field.required) " *" else "")
    var choosing by remember { mutableStateOf(false) }
    var chosenLabel by remember(field.key,value) { mutableStateOf("") }
    if(field.kind==Kind.BOOLEAN) {
        Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
            Switch(value=="true",{onValue(it.toString())},enabled=enabled)
            Text(title,Modifier.padding(start=10.dp))
        };return
    }
    if(field.kind in setOf(Kind.RELATION,Kind.CHOICE,Kind.MULTI)) {
        if(field.kind==Kind.RELATION && value.isNotBlank()) LaunchedEffect(field.key,value,values[field.parent]) {
            try { chosenLabel=model.lookup(field,values).firstOrNull{it.first==value}?.second ?: "Saved selection" }
            catch (_: Exception) { chosenLabel="Saved selection" }
        }
        val shown=when(field.kind) {
            Kind.RELATION -> if(value.isBlank()) "Choose" else chosenLabel.ifBlank{"Loading selection…"}
            Kind.MULTI -> if(value.isBlank()||value=="[]") "Choose" else {
                val a=runCatching{JSONArray(value)}.getOrDefault(JSONArray())
                if(field.options.isNotEmpty()) AppModel.strings(a).joinToString(", ") else a.length().toString()+" selected"
            }
            else -> when { field.key=="status" && value=="0" -> "Active";field.key=="status" && value=="1" -> "Suspended";else -> value.ifBlank{"Choose"} }
        }
        OutlinedCard(onClick={if(enabled)choosing=true},modifier=Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp)) { Text(title,style=MaterialTheme.typography.labelMedium)
                Text(shown);if(error!=null)Text(error,color=MaterialTheme.colorScheme.error) }
        }
        if(choosing) ChoiceDialog(model,field,values,{choosing=false}) { id,text -> chosenLabel=text;onValue(id);choosing=false }
        return
    }
    val dateOrTime=field.kind in setOf(Kind.DATE,Kind.TIME)
    val keyboard=when(field.kind) {
        Kind.DECIMAL -> KeyboardType.Decimal
        Kind.INTEGER -> KeyboardType.Number
        Kind.PHONE -> KeyboardType.Phone
        Kind.EMAIL -> KeyboardType.Email
        else -> KeyboardType.Text
    }
    OutlinedTextField(value,onValue,modifier=Modifier.fillMaxWidth(),enabled=enabled,label={Text(title)},
        singleLine=field.kind!=Kind.LONG,minLines=if(field.kind==Kind.LONG)3 else 1,
        isError=error!=null,supportingText={if(error!=null)Text(error)},
        keyboardOptions=KeyboardOptions(keyboardType=keyboard),trailingIcon={
            if(dateOrTime) IconButton(onClick={
                if(field.kind==Kind.DATE) {
                    val day=runCatching{LocalDate.parse(value)}.getOrDefault(LocalDate.now())
                    DatePickerDialog(context,{_,y,m,d->onValue(LocalDate.of(y,m+1,d).toString())},
                        day.year,day.monthValue-1,day.dayOfMonth).show()
                } else {
                    val time=runCatching{LocalTime.parse(value)}.getOrDefault(LocalTime.of(9,0))
                    TimePickerDialog(context,{_,h,m->onValue(LocalTime.of(h,m).format(DateTimeFormatter.ofPattern("HH:mm")))},
                        time.hour,time.minute,true).show()
                }
            },enabled=enabled) { Icon(if(field.kind==Kind.DATE)Icons.Default.CalendarMonth else Icons.Default.Schedule, "Choose "+field.label) }
        })
}
@Composable private fun ChoiceDialog(model: AppModel,field: Field,values: Map<String,String>,dismiss: ()->Unit,
    choose: (String,String)->Unit) {
    var choices by remember { mutableStateOf<List<Pair<String,String>>>(emptyList()) }
    var loading by remember { mutableStateOf(true) };var error by remember { mutableStateOf<String?>(null) }
    var retry by remember { mutableIntStateOf(0) };var search by remember { mutableStateOf("") }
    var selected by remember {
        mutableStateOf(runCatching{AppModel.strings(JSONArray(values[field.key].orEmpty()))}.getOrDefault(emptyList()).toSet())
    }
    LaunchedEffect(field.key,retry) {
        loading=true;error=null
        try { choices=model.lookup(field,values) } catch(e: Exception) { error=e.message }
        loading=false
    }
    AlertDialog(onDismissRequest=dismiss,title={Text(field.label)},text={
        Column(Modifier.heightIn(max=420.dp)) {
            OutlinedTextField(search,{search=it},label={Text("Search choices")},singleLine=true)
            if(loading) CircularProgressIndicator(Modifier.padding(16.dp))
            if(error!=null) { Text(error!!);TextButton(onClick={retry++}) { Text("Try again") } }
            LazyColumn {
                val filtered=choices.filter{it.second.contains(search,true)}
                if(!loading&&error==null&&filtered.isEmpty()) item { Text(if(field.parent.isNotBlank()&&values[field.parent].isNullOrBlank())
                    "Choose "+field.parent+" first." else "No matching choices.",Modifier.padding(12.dp)) }
                items(filtered) { (id,title) ->
                    Row(Modifier.fillMaxWidth().clickable {
                        if(field.kind==Kind.MULTI) selected=if(id in selected)selected-id else selected+id else choose(id,title)
                    }.padding(vertical=8.dp),verticalAlignment=Alignment.CenterVertically) {
                        if(field.kind==Kind.MULTI) Checkbox(id in selected,null)
                        Text(when{field.key=="status"&&id=="0"->"Active";field.key=="status"&&id=="1"->"Suspended";else->title})
                    }
                }
            }
        }
    },confirmButton={
        if(field.kind==Kind.MULTI) TextButton(onClick={
            val array=JSONArray()
            selected.forEach{array.put(if(field.source.isNotBlank())it.toLong() else it)}
            choose(array.toString(),selected.size.toString()+" selected")
        }) { Text("Done") } else TextButton(onClick=dismiss) { Text("Close") }
    },dismissButton={
        if(!field.required) TextButton(onClick={choose(if(field.kind==Kind.MULTI)"[]" else "","")}) { Text("Clear selection") }
    })
}

@Composable private fun Login(model: AppModel) {
    val registrationFields=listOf(
        t("name","Full name",true),Field("email","Email",Kind.EMAIL),
        choice("gender","Gender","Male","Female","Other"),
        choice("farmer_type","Farmer category","individual","company",default="individual"),
        t("company_name","Company name"),
        rel("state_id","State","registration/states/",true),
        rel("district_id","District","registration/districts/",true,"state_id","state_id"),
        t("taluk","Taluk"),t("village","Village"),Field("pincode","Pincode",maxLength=6),t("address","Address")
    )
    var mobile by rememberSaveable { mutableStateOf("") }
    var otp by remember { mutableStateOf("") }
    var details by rememberSaveable { mutableStateOf(Validation.initial(registrationFields,null)) }
    var attempted by remember { mutableStateOf(false) }
    var countdown by remember { mutableLongStateOf(0) }
    LaunchedEffect(model.otpResendAt) {
        while(true) {
            countdown=((model.otpResendAt-System.currentTimeMillis())/1000).coerceAtLeast(0)
            if(countdown==0L)break
            kotlinx.coroutines.delay(1000)
        }
    }
    val fields=registrationFields.map{if(it.key in setOf("gender","farmer_type") ||
        (it.key=="company_name"&&details["farmer_type"]=="company"))it.copy(required=true) else it}
    val errors=Validation.errors(fields,details)
    Column(Modifier.fillMaxSize().systemBarsPadding().imePadding().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement=Arrangement.spacedBy(14.dp)) {
        Spacer(Modifier.height(28.dp))
        Icon(Icons.Default.Grass,null,tint=green,modifier=Modifier.size(48.dp))
        Text("AgriOT",style=MaterialTheme.typography.displaySmall,fontWeight=FontWeight.Bold,color=green)
        Text("A clearer day on the farm.",style=MaterialTheme.typography.titleLarge)
        Text("Review build · Connected to the existing development server",style=MaterialTheme.typography.labelMedium)
        HorizontalDivider(Modifier.padding(vertical=12.dp))
        Text(if(model.registration)"Create your farmer account" else "Welcome back",style=MaterialTheme.typography.headlineSmall)
        model.error?.let { ErrorNotice(it,model.uncertain) }
        model.message?.let { Notice(it) }
        OutlinedTextField(mobile,{mobile=it.filter(Char::isDigit).take(10)},Modifier.fillMaxWidth(),
            enabled=!model.busy&&!model.otpSent,label={Text("Mobile number")},singleLine=true,
            prefix={Text("+91 ")},keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Phone),
            isError=attempted&&!Validation.phone(mobile),
            supportingText={if(attempted&&!Validation.phone(mobile))Text("Enter a valid 10-digit mobile number")})
        if(model.registration&&!model.otpSent) fields.forEach { field ->
            if(field.key!="company_name"||details["farmer_type"]=="company")
                FormField(model,field,details,if(attempted)errors[field.key] else null,!model.busy) { v ->
                    details=details.toMutableMap().apply {
                        put(field.key,v)
                        fields.filter{it.parent==field.key}.forEach{put(it.key,"")}
                    }
                }
        }
        if(model.otpSent) {
            Text("Enter the code sent to +91 "+mobile)
            OutlinedTextField(otp,{otp=it.filter(Char::isDigit).take(6)},Modifier.fillMaxWidth(),
                enabled=!model.busy,label={Text("Six-digit verification code")},singleLine=true,
                visualTransformation=PasswordVisualTransformation(),keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.NumberPassword))
            Button(onClick={model.verifyOtp(mobile,otp)},enabled=!model.busy&&otp.length==6,
                modifier=Modifier.fillMaxWidth().height(52.dp)) { Text("Verify and continue") }
            TextButton(onClick={otp="";model.sendOtp(mobile,details)},enabled=!model.busy&&countdown==0L) {
                Text(if(countdown>0)"Request another code in "+countdown+"s" else "Request another code")
            }
            TextButton(onClick={otp="";model.changeLoginMode(model.registration)},enabled=!model.busy) { Text("Change mobile number") }
        } else Button(onClick={
            attempted=true
            if(Validation.phone(mobile)&&(!model.registration||errors.isEmpty()))model.sendOtp(mobile,details)
        },enabled=!model.busy,modifier=Modifier.fillMaxWidth().height(52.dp)) {
            Text(if(model.busy)"Please wait…" else "Send verification code")
        }
        if(model.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        TextButton(onClick={attempted=false;otp="";model.changeLoginMode(!model.registration)},enabled=!model.busy) {
            Text(if(model.registration)"Already registered? Sign in" else "New farmer? Create an account")
        }
        Text("Only enter the verification code you receive on your own phone.",style=MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(24.dp))
    }
}
private fun harvestAction(row: JSONObject) = Screen.Action("Record harvest",
    "crop-splits/"+row.get("id")+"/finalize_harvest/",listOf(
        d("yield_tons","Harvested quantity",true,true),
        choice("unit","Quantity unit","Kg","Ton","Quintal",default=row.optString("expected_yield_unit","Kg")),
        d("price_per_mt","Price per selected unit (INR)",default="0"),
        t("quality","Quality grade",true).copy(default="Grade 1")
    ),"harvest",row)
private fun saleAction(row: JSONObject) = Screen.Action("Record crop sale","sales/create/",listOf(
    rel("customer_id","Customer","customers/",true),
    d("load_weight","Quantity sold",true,true),
    Field("unit","Quantity unit",Kind.CHOICE,true,options=listOf(row.optString("expected_yield_unit","Kg")),default=row.optString("expected_yield_unit","Kg")),
    t("grade","Harvest quality grade",true).copy(default="Grade 1"),
    d("unit_rate","Price per selected unit (INR)",true,true),
    d("gst_rate","GST (%)",true,default="0"),d("discount","Discount (INR)",true,default="0"),
    d("commission","Commission (INR)",true,default="0"),
    d("paid_amount","Amount already paid (INR)",true,default="0"),payment,
    date("close_date","Sale date",true),date("due_date","Balance due date",true)
),"sale",row)
private fun settleAction(row: JSONObject) = Screen.Action("Record invoice payment",
    "outstanding/"+row.get("id")+"/pay/",listOf(
        d("amount_paid","Payment amount (INR)",true,true),payment,t("notes","Payment note")
    ),"settle",row)
@Composable private fun DocumentUpload(model: AppModel,dismiss: ()->Unit) {
    val context=LocalContext.current;val scope=rememberCoroutineScope()
    var title by remember { mutableStateOf("") };var category by remember { mutableStateOf("") }
    var chosen by remember { mutableStateOf<Uri?>(null) };var error by remember { mutableStateOf<String?>(null) }
    var reading by remember { mutableStateOf(false) }
    val picker=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> chosen=uri }
    AlertDialog(onDismissRequest={if(!reading)dismiss()},title={Text("Upload document")},text={
        Column(verticalArrangement=Arrangement.spacedBy(12.dp)) {
            OutlinedTextField(title,{title=it},label={Text("Document title *")},singleLine=true)
            OutlinedTextField(category,{category=it},label={Text("Category")},singleLine=true)
            OutlinedButton(onClick={picker.launch(arrayOf("application/pdf","image/jpeg","image/png"))},enabled=!reading) {
                Text(if(chosen==null)"Choose PDF or image" else "File selected · choose another")
            }
            Text("Maximum file size: 10 MB",style=MaterialTheme.typography.bodySmall)
            error?.let { Text(it,color=MaterialTheme.colorScheme.error) }
            if(reading) LinearProgressIndicator()
        }
    },confirmButton={TextButton(onClick={
        if(chosen==null||title.isBlank()) { error="Enter a title and select a file";return@TextButton }
        reading=true;error=null
        scope.launch {
            try {
                val uri=chosen!!
                val data=withContext(Dispatchers.IO) {
                    val resolver=context.contentResolver
                    val mime=resolver.getType(uri) ?: "application/octet-stream"
                    require(mime in setOf("application/pdf","image/jpeg","image/png")) { "Choose a PDF, JPEG or PNG file" }
                    val name=resolver.query(uri,arrayOf(android.provider.OpenableColumns.DISPLAY_NAME),null,null,null)?.use {
                        if(it.moveToFirst())it.getString(0) else null
                    } ?: "document"
                    val bytes=resolver.openInputStream(uri)?.use { input ->
                        val out=java.io.ByteArrayOutputStream()
                        val buffer=ByteArray(8192)
                        while(true) {
                            val n=input.read(buffer);if(n<0)break
                            require(out.size()+n<=10*1024*1024) { "Choose a file smaller than 10 MB" }
                            out.write(buffer,0,n)
                        };out.toByteArray()
                    } ?: throw IllegalArgumentException("Unable to open this file")
                    Triple(name,mime,bytes)
                }
                model.uploadDocument(mapOf("title" to title.trim(),"category" to category.trim()),data.first,data.second,data.third)
                dismiss()
            } catch(e: Exception) { error=e.message }
            finally { reading=false }
        }
    },enabled=!reading&&!model.busy) { Text("Upload") }},
        dismissButton={TextButton(onClick=dismiss,enabled=!reading) { Text("Cancel") }})
}
