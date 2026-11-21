package com.agriot.farmer.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.agriot.farmer.BuildConfig
import java.io.IOException
import java.security.KeyStore
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener

class ApiFailure(val status: Int, message: String, val uncertain: Boolean = false) : Exception(message)

interface Vault {
    fun get(key: String): String?
    fun put(key: String, value: String)
    fun clear()
    fun clearCache()
}

/** Every persisted token and response is encrypted with a device-bound Android Keystore key. */
class DeviceVault(context: Context) : Vault {
    private val prefs = context.getSharedPreferences("agriot_private", Context.MODE_PRIVATE)
    private val alias = "agriot_review_aes_v1"
    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    override fun get(key: String): String? {
        val raw = prefs.getString(key, null) ?: return null
        return try {
            val parts = raw.split(":")
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)))
            String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), Charsets.UTF_8)
        } catch (_: Exception) {
            // A lost/invalidated key never falls back to plaintext.
            prefs.edit().clear().commit()
            null
        }
    }
    override fun put(key: String, value: String) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encrypted = Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + ":" +
            Base64.encodeToString(cipher.doFinal(value.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)
        check(prefs.edit().putString(key, encrypted).commit()) { "Unable to save secure session" }
    }
    override fun clear() { prefs.edit().clear().commit() }
    override fun clearCache() {
        val editor = prefs.edit()
        prefs.all.keys.filter { it.startsWith("cache_") }.forEach { editor.remove(it) }
        editor.commit()
    }
}

data class Loaded(val value: Any, val cachedAt: Long? = null)
data class Page(val rows: List<JSONObject>, val next: String?)

/** No interceptor logs credentials; POST/PATCH/DELETE are never automatically retried. */
class FarmerApi(
    private val vault: Vault,
    baseUrl: String = BuildConfig.API_BASE_URL,
    private val allowInsecureForTests: Boolean = false
) {
    private val base = baseUrl.toHttpUrl()
    private val client = OkHttpClient.Builder()
        .retryOnConnectionFailure(false).followRedirects(false).followSslRedirects(false)
        .connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS)
        .callTimeout(45, TimeUnit.SECONDS).build()
    init { require(base.scheme == "https" || allowInsecureForTests) }
    val signedIn: Boolean get() = !vault.get("session").isNullOrBlank()
    fun token() = vault.get("session")
    fun acceptToken(token: String) {
        require(token.count { it == '.' } == 2) { "Server did not return a valid session" }
        vault.clear()
        vault.put("session", token)
    }
    fun signOut() = vault.clear()
    private fun namespace(): String = try {
        val payload = token()?.split(".")?.get(1) ?: ""
        val obj = JSONObject(String(Base64.decode(payload, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)))
        obj.optString("user_id") + ":" + obj.optString("farmer_id") + ":" + obj.optString("profile_type")
    } catch (_: Exception) { "session" }
    fun managerSession(): Boolean = try {
        val payload = token()?.split(".")?.get(1) ?: ""
        JSONObject(String(Base64.decode(payload, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)))
            .optString("profile_type") == "manager"
    } catch (_: Exception) { false }
    private fun url(path: String): HttpUrl {
        val resolved = base.resolve(path) ?: throw IllegalArgumentException("Invalid API address")
        require(resolved.scheme == base.scheme && resolved.host == base.host && resolved.port == base.port &&
            resolved.encodedPath.startsWith(base.encodedPath) && resolved.username.isEmpty() && resolved.password.isEmpty()) {
            "Server returned an address outside this AgriOT API"
        }
        return resolved
    }
    private fun cacheKey(path: String): String = "cache_" + MessageDigest.getInstance("SHA-256")
        .digest((base.toString() + namespace() + path).toByteArray()).joinToString("") { "%02x".format(it) }

    suspend fun read(path: String, cache: Boolean = true): Loaded {
        val key = cacheKey(path)
        try {
            val result = request("GET", path)
            if (cache && signedIn) vault.put(key, JSONObject().put("at", System.currentTimeMillis()).put("value", result).toString())
            return Loaded(result)
        } catch (e: ApiFailure) {
            if (cache && e.status == 0 && signedIn) {
                vault.get(key)?.let {
                    val stored = JSONObject(it)
                    return Loaded(stored.get("value"), stored.getLong("at"))
                }
            }
            throw e
        }
    }
    suspend fun write(path: String, body: JSONObject, method: String = "POST"): Any {
        val result = request(method, path, body)
        vault.clearCache()
        return result
    }
    suspend fun form(path: String, values: Map<String, String>): Any =
        execute("POST", path, FormBody.Builder().apply { values.forEach { (k,v) -> add(k,v) } }.build())

    suspend fun upload(path: String, values: Map<String, String>, name: String, mime: String, bytes: ByteArray): Any {
        require(bytes.size <= 10 * 1024 * 1024) { "Choose a file smaller than 10 MB" }
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .apply { values.forEach { (k,v) -> addFormDataPart(k,v) } }
            .addFormDataPart("file", name, bytes.toRequestBody(mime.toMediaType())).build()
        val result = execute("POST", path, body)
        vault.clearCache()
        return result
    }
    private suspend fun request(method: String, path: String, body: JSONObject? = null): Any =
        execute(method, path, if (method == "GET") null else
            (body ?: JSONObject()).toString().toRequestBody("application/json; charset=utf-8".toMediaType()))

    private suspend fun execute(method: String, path: String, body: RequestBody?): Any {
        val req = Request.Builder().url(url(path)).header("Accept", "application/json")
            .apply { token()?.let { header("Authorization", "Bearer " + it) } }
            .method(method, body).build()
        return suspendCancellableCoroutine { continuation ->
            val call = client.newCall(req)
            continuation.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    if (continuation.isActive) continuation.resumeWithException(ApiFailure(0,
                        if (method == "GET") "Unable to connect. Check your connection and try again."
                        else "The connection ended before confirmation. Refresh the records before trying again; this change may have been saved.",
                        method != "GET"))
                }
                override fun onResponse(call: Call, response: Response) {
                    response.use {
                        try {
                            val text = it.body?.string().orEmpty()
                            val data = try { if (text.isBlank()) JSONObject() else JSONTokener(text).nextValue() }
                                catch (_: Exception) { throw ApiFailure(it.code, if(method=="GET") "The server returned an unreadable response. Please try again later." else "The server confirmation could not be read. Refresh the records before submitting again.",method!="GET") }
                            if (!it.isSuccessful) {
                                if (it.code == 401) vault.clear()
                                throw ApiFailure(it.code, if (it.code == 401) "Your session has ended. Sign in again."
                                    else if (it.code in 300..399) "The API redirected this request. Please check the configured server."
                                    else errorText(data, "Request failed (" + it.code + ")"),method!="GET" && it.code>=500)
                            }
                            if (data is JSONObject && (data.opt("success") == false || data.has("error")))
                                throw ApiFailure(it.code, errorText(data, "The server could not save this change."))
                            if (continuation.isActive) continuation.resume(data)
                        } catch (e: Exception) {
                            if (continuation.isActive) continuation.resumeWithException(e)
                        }
                    }
                }
            })
        }
    }
    companion object {
        fun errorText(value: Any?, fallback: String): String = when(value) {
            is JSONObject -> value.keys().asSequence().filter { it !in setOf("success", "status") }.take(8)
                .joinToString("\n") { k -> (if (k in setOf("detail","error","message","non_field_errors")) "" else k.replace('_',' ') + ": ") +
                    errorText(value.opt(k), "") }.ifBlank { fallback }
            is JSONArray -> (0 until value.length()).joinToString("; ") { errorText(value.opt(it), "") }
            is String -> value.take(500).takeUnless { it.contains("<html", true) } ?: fallback
            else -> fallback
        }
        fun page(value: Any): Page {
            if (value is JSONArray) return Page((0 until value.length()).mapNotNull { value.optJSONObject(it) }, null)
            val obj = value as? JSONObject ?: return Page(emptyList(), null)
            val arr = obj.optJSONArray("results") ?: obj.optJSONArray("data") ?: obj.optJSONArray("notifications") ?: JSONArray()
            return Page((0 until arr.length()).mapNotNull { arr.optJSONObject(it) }.map { row ->
                if (!row.has("id") && row.has("notification_id")) row.put("id",row.get("notification_id"))
                row
            },
                obj.optString("next").takeIf { it.isNotBlank() && it != "null" })
        }
    }
}
