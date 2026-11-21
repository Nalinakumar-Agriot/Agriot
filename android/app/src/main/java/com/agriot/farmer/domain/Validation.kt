package com.agriot.farmer.domain

import java.math.BigDecimal
import java.math.RoundingMode
import java.time.LocalDate
import java.time.LocalTime
import org.json.JSONArray
import org.json.JSONObject

object Validation {
    fun decimal(value: String): BigDecimal? = value.trim().toBigDecimalOrNull()
    fun phone(value: String) = Regex("^[6-9][0-9]{9}$").matches(value)
    fun display(value: Any?): String = when(value) {
        null, JSONObject.NULL -> ""
        is JSONArray -> (0 until value.length()).joinToString(", ") { display(value.opt(it)) }
        is JSONObject -> value.optString("name", value.optString("title", value.toString()))
        is Boolean -> if (value) "Yes" else "No"
        else -> value.toString()
    }
    fun initial(fields: List<Field>, row: JSONObject?): Map<String,String> = fields.associate { field ->
        val value = row?.opt(field.key)
        val text = if (value == null || value == JSONObject.NULL) {
            if (row == null && field.kind == Kind.DATE && field.required) LocalDate.now().toString() else
                if (row == null) field.default else ""
        } else if (field.kind == Kind.MULTI && value is JSONArray) value.toString()
        else value.toString()
        field.key to if (field.key == "amount") decimal(text)?.abs()?.toPlainString().orEmpty() else text
    }
    fun errors(fields: List<Field>, values: Map<String,String>): Map<String,String> {
        val result = mutableMapOf<String,String>()
        fields.forEach { field ->
            val raw = values[field.key].orEmpty().trim()
            val n = decimal(raw)
            val error = when {
                raw.isBlank() -> if (field.required) "Enter " + field.label.lowercase() else null
                raw.length > field.maxLength && field.kind !in setOf(Kind.MULTI,Kind.LONG) -> "Use at most " + field.maxLength + " characters"
                field.kind == Kind.LONG && raw.length > field.maxLength -> "Use at most " + field.maxLength + " characters"
                field.kind == Kind.PHONE && !phone(raw) -> "Enter a valid 10-digit Indian mobile number"
                field.kind == Kind.EMAIL && !Regex("^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$").matches(raw) -> "Enter a valid email address"
                field.kind == Kind.DATE && runCatching { LocalDate.parse(raw) }.isFailure -> "Choose a valid date"
                field.kind == Kind.TIME && runCatching { LocalTime.parse(raw) }.isFailure -> "Choose a valid time"
                field.kind in setOf(Kind.DECIMAL,Kind.INTEGER) && (n == null || !Regex("^-?[0-9]+(\\.[0-9]+)?$").matches(raw)) -> "Enter a valid number"
                field.kind == Kind.INTEGER && n != null && n.stripTrailingZeros().scale() > 0 -> "Enter a whole number"
                field.positive && n != null && n <= BigDecimal.ZERO -> "Enter a value greater than zero"
                field.nonNegative && n != null && n < BigDecimal.ZERO -> "Enter zero or a positive value"
                field.key == "latitude" && n != null && (n < BigDecimal("-90") || n > BigDecimal("90")) -> "Latitude must be between -90 and 90"
                field.key == "longitude" && n != null && (n < BigDecimal("-180") || n > BigDecimal("180")) -> "Longitude must be between -180 and 180"
                field.key == "soil_ph" && n != null && (n < BigDecimal.ZERO || n > BigDecimal("14")) -> "Soil pH must be between 0 and 14"
                field.key == "pincode" && !Regex("^[1-9][0-9]{5}$").matches(raw) -> "Enter a valid 6-digit pincode"
                field.key == "total_flushes" && n != null && n > BigDecimal("50") -> "Use 1 to 50 flushes"
                field.key in setOf("amount","unit_price","rental_price","base_daily_rate","salary","balance_amount","acreage","expected_yield") && n != null && n.stripTrailingZeros().scale() > 2 -> "Use at most 2 decimal places"
                else -> null
            }
            if (error != null) result[field.key] = error
        }
        val start = values["start_date"].orEmpty()
        val end = values["end_date"].orEmpty()
        if (start.isNotBlank() && end.isNotBlank() && end < start) result["end_date"] = "End date must follow the start date"
        val from = values["start_time"].orEmpty()
        val to = values["end_time"].orEmpty()
        if (from.isNotBlank() && to.isNotBlank() && end <= start && to <= from) result["end_time"] = "End time must follow the start time"
        values["date_of_birth"]?.takeIf { it.isNotBlank() && it > LocalDate.now().toString() }?.let {
            result["date_of_birth"] = "Date of birth cannot be in the future"
        }
        val total = decimal(values["amount"].orEmpty())
        val due = decimal(values["balance_amount"].orEmpty())
        if (total != null && due != null && due > total.abs()) result["balance_amount"] = "Balance cannot exceed the expense"
        return result
    }
    /** Only changed fields go into PATCH; loading lookup options cannot wipe stored FK values. */
    fun payload(fields: List<Field>, values: Map<String,String>, original: Map<String,String>? = null): JSONObject {
        val result = JSONObject()
        fields.forEach { field ->
            val value = values[field.key].orEmpty().trim()
            if (original != null && value == original[field.key].orEmpty().trim()) return@forEach
            if (value.isBlank()) {
                if (original != null) result.put(field.key,
                    if (field.kind in setOf(Kind.RELATION,Kind.DATE,Kind.TIME,Kind.DECIMAL,Kind.INTEGER)) JSONObject.NULL else "")
                return@forEach
            }
            result.put(field.key, when(field.kind) {
                Kind.BOOLEAN -> value == "true"
                Kind.MULTI -> JSONArray(value)
                Kind.RELATION,Kind.INTEGER -> value.toLong()
                else -> value
            })
        }
        return result
    }
    fun saleTotal(subtotal: BigDecimal, gstPercent: BigDecimal, discount: BigDecimal, commission: BigDecimal): BigDecimal =
        (subtotal + subtotal * gstPercent / BigDecimal("100") - discount - commission).setScale(2,RoundingMode.HALF_UP)
    fun limit(value: String): BigDecimal? = value.trim().takeUnless {
        it.equals("unlimited",true) || it.equals("none",true) || it.isBlank() || it == "null"
    }?.toBigDecimalOrNull()
}
