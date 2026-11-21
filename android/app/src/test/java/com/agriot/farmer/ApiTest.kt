package com.agriot.farmer

import com.agriot.farmer.data.*
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.After
import org.junit.Before
import org.junit.Test

class ApiTest {
    class MemoryVault : Vault {
        val data=mutableMapOf<String,String>()
        override fun get(key: String)=data[key]
        override fun put(key: String,value: String) { data[key]=value }
        override fun clear() { data.clear() }
        override fun clearCache() { data.keys.filter{it.startsWith("cache_")}.toList().forEach{data.remove(it)} }
    }
    private lateinit var server: MockWebServer
    private lateinit var vault: MemoryVault
    private lateinit var api: FarmerApi
    @Before fun before() {
        server=MockWebServer();server.start();vault=MemoryVault()
        api=FarmerApi(vault,server.url("/api/farmer/").toString(),true)
    }
    @After fun after() { server.shutdown() }
    @Test fun otpUsesFormEncodingAndSessionIsOnlyAddedAfterVerification() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"message":"OTP sent successfully"}"""))
        api.form("login/send-otp/",mapOf("mobile" to "9876543210"))
        val sent=server.takeRequest()
        assertEquals("mobile=9876543210",sent.body.readUtf8())
        assertNull(sent.getHeader("Authorization"))
        assertTrue(sent.getHeader("Content-Type")!!.startsWith("application/x-www-form-urlencoded"))
        api.acceptToken("header.payload.signature")
        server.enqueue(MockResponse().setBody("""{"id":14}"""))
        api.read("farms/14/",false)
        assertEquals("Bearer header.payload.signature",server.takeRequest().getHeader("Authorization"))
    }
    @Test fun unauthorizedResponseClearsSessionAndCachedFarmData() = runBlocking {
        api.acceptToken("header.payload.signature");vault.put("cache_old","private")
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"Expired"}"""))
        try { api.read("farms/",false);fail("Expected expired session") } catch(e: ApiFailure) { assertEquals(401,e.status) }
        assertFalse(api.signedIn);assertTrue(vault.data.isEmpty())
    }
    @Test fun nextLinksCannotSendCredentialsToAnotherHostOrOutsideFarmerApi() = runBlocking {
        api.acceptToken("header.payload.signature")
        for(path in listOf("https://example.com/api/farmer/farms/","../../admin/","//example.com/farms/")) {
            try { api.read(path,false);fail("Expected address rejection") } catch(_: IllegalArgumentException) {}
        }
        assertEquals(0,server.requestCount)
    }
    @Test fun failedMutationIsNotAutomaticallyRetriedAndIsReportedAsUncertain() = runBlocking {
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST))
        try { api.write("farms/",JSONObject().put("land_name","Farm"));fail("Expected uncertain result") }
        catch(e: ApiFailure) { assertTrue(e.uncertain) }
        assertEquals(1,server.requestCount)
    }
    @Test fun forbiddenResponseCannotFallBackToStaleCachedData() = runBlocking {
        api.acceptToken("header.payload.signature")
        server.enqueue(MockResponse().setBody("""{"results":[{"id":14}],"next":null}"""))
        api.read("farms/")
        server.enqueue(MockResponse().setResponseCode(403).setBody("""{"detail":"No access"}"""))
        try { api.read("farms/");fail("Expected permission failure") } catch(e: ApiFailure) { assertEquals(403,e.status) }
    }
    @Test fun serverFailureAfterWriteIsAlsoAnUncertainOutcome() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500).setBody("""{"error":"Unable to finish response"}"""))
        try { api.write("expenses/",JSONObject().put("amount","-100"));fail("Expected uncertain result") }
        catch(e: ApiFailure) { assertEquals(500,e.status);assertTrue(e.uncertain) }
        assertEquals(1,server.requestCount)
    }
    @Test fun notificationIdentifiersAreNormalizedForPaginationAndReadActions() {
        val page=FarmerApi.page(JSONObject("""{"notifications":[{"notification_id":51,"name":"Task reminder"},{"notification_id":52,"name":"New task"}],"next":null}"""))
        assertEquals(2,page.rows.size)
        assertEquals(51,page.rows[0].getInt("id"))
        assertEquals(52,page.rows[1].getInt("id"))
    }
    @Test fun paginatedResultsKeepNextLinkAndRows() {
        val page=FarmerApi.page(JSONObject("""{"results":[{"id":14},{"id":15}],"next":"/api/farmer/farms/?page=2"}"""))
        assertEquals(2,page.rows.size);assertEquals("/api/farmer/farms/?page=2",page.next)
    }
}
