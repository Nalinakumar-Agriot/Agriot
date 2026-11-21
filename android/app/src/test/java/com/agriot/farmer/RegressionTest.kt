package com.agriot.farmer

import com.agriot.farmer.domain.*
import java.math.BigDecimal
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class RegressionTest {
    @Test fun negativeFarmAreaAndInvalidMobileNeverReachTheApi() {
        val farm=Catalog.module("farms")
        val invalid=mapOf("land_name" to "North farm","total_area" to "-1","latitude" to "13","longitude" to "77")
        assertTrue(Validation.errors(farm.fields,invalid).containsKey("total_area"))
        val employee=Catalog.module("users")
        assertTrue(Validation.errors(employee.fields,mapOf("name" to "Arun","mobile_number" to "0000000000","role" to "Worker","base_daily_rate" to "500"))
            .containsKey("mobile_number"))
        assertFalse(Validation.phone("1234567890"))
        assertTrue(Validation.phone("9876543210"))
    }
    @Test fun editingNotesPreservesUnloadedLocationSelectionsAndUnmodeledData() {
        val module=Catalog.module("farms")
        val row=JSONObject("""{"id":14,"land_name":"North farm","total_area":"2","state":23,"district":114,"taluk":5,"notes":"Old","water_sources":[{"type":"well"}]}""")
        val original=Validation.initial(module.fields,row)
        val updated=original+("notes" to "Updated")
        val patch=Validation.payload(module.fields,updated,original)
        assertEquals(1,patch.length())
        assertEquals("Updated",patch.getString("notes"))
        assertFalse(patch.has("state"));assertFalse(patch.has("district"));assertFalse(patch.has("taluk"))
        assertFalse(patch.has("water_sources"))
    }
    @Test fun explicitLocationClearIsDifferentFromAnUnchangedLocation() {
        val field=rel("district","District","dropdowns/districts/")
        val patch=Validation.payload(listOf(field),mapOf("district" to ""),mapOf("district" to "12"))
        assertTrue(patch.has("district"))
        assertTrue(patch.isNull("district"))
    }
    @Test fun limitsAndSaleTotalsDoNotProduceNaNOrFloatingPointRoundingErrors() {
        assertNull(Validation.limit("unlimited"))
        assertEquals(BigDecimal("0"),Validation.limit("0"))
        assertEquals(BigDecimal("900.00"),Validation.saleTotal(BigDecimal("1000"),BigDecimal("5"),BigDecimal("100"),BigDecimal("50")))
        assertEquals(BigDecimal("0.30"),Validation.saleTotal(BigDecimal("0.1"),BigDecimal("200"),BigDecimal.ZERO,BigDecimal.ZERO))
    }
    @Test fun southernCoordinatesAndDateOrderAreValidatedIndependently() {
        val fields=Catalog.module("farms").fields
        assertFalse(Validation.errors(fields,mapOf("land_name" to "Farm","total_area" to "2","latitude" to "-35","longitude" to "-78")).containsKey("latitude"))
        assertTrue(Validation.errors(fields,mapOf("land_name" to "Farm","total_area" to "2","latitude" to "91")).containsKey("latitude"))
        assertTrue(Validation.errors(Catalog.module("tasks").fields,mapOf("title" to "Water","farm" to "1","task_date" to "2026-11-21",
            "start_date" to "2026-11-22","end_date" to "2026-11-21")).containsKey("end_date"))
    }
    @Test fun managerRequiresBothAnEnabledSubscriptionModuleAndAnExplicitGrant() {
        assertFalse(Catalog.allowed("Sales",setOf("Sales"),true,setOf("Farms")))
        assertFalse(Catalog.allowed("Sales",setOf("Farms"),true,setOf("ALL")))
        assertTrue(Catalog.allowed("Farms",setOf("Farms & Crop Splits"),true,setOf("Farms")))
        assertTrue(Catalog.allowed("Inventory",setOf("Inventory"),false,emptySet()))
    }
}
