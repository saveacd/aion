package dev.aion.app.data

import kotlinx.serialization.json.Json
import okhttp3.Authenticator
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.Route
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit

private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()

class AuthInterceptor(private val session: SessionStore) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val path = request.url.encodedPath
        val token = session.accessToken
        if (token.isNullOrBlank() || path.endsWith("/auth/login") || path.endsWith("/auth/refresh")) {
            return chain.proceed(request)
        }
        return chain.proceed(
            request.newBuilder().header("Authorization", "Bearer $token").build(),
        )
    }
}

/**
 * Refreshes the access token once per 401. A single refresh call is allowed at
 * a time; when it fails the local session is dropped and the UI returns to the
 * login screen instead of looping.
 */
class TokenAuthenticator(
    private val session: SessionStore,
    private val json: Json,
) : Authenticator {

    private val lock = Any()
    private val refreshClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    override fun authenticate(route: Route?, response: Response): Request? {
        val path = response.request.url.encodedPath
        if (path.endsWith("/auth/login") || path.endsWith("/auth/refresh")) return null
        if (countPrior(response) >= 2) return null
        val refresh = session.refreshToken ?: run {
            session.expire()
            return null
        }

        return synchronized(lock) {
            val latest = session.accessToken
            val attempted = response.request.header("Authorization")
            if (!latest.isNullOrBlank() && attempted != null && attempted != "Bearer $latest") {
                // Another thread already refreshed while this call was in flight.
                return response.request.newBuilder()
                    .header("Authorization", "Bearer $latest")
                    .build()
            }

            val base = session.baseUrl.trimEnd('/')
            if (base.isBlank()) {
                session.expire()
                return null
            }

            val payload = json.encodeToString(
                RefreshRequest.serializer(),
                RefreshRequest(refresh),
            )
            val request = Request.Builder()
                .url("$base/api/v1/auth/refresh")
                .post(payload.toRequestBody(JSON_MEDIA_TYPE))
                .build()

            val token = try {
                refreshClient.newCall(request).execute().use { call ->
                    if (!call.isSuccessful) return@synchronized null
                    val body = call.body?.string() ?: return@synchronized null
                    json.decodeFromString(TokenResponse.serializer(), body)
                }
            } catch (e: Exception) {
                null
            }

            if (token == null || token.accessToken.isBlank()) {
                session.expire()
                return null
            }
            session.saveTokens(token)

            return response.request.newBuilder()
                .header("Authorization", "Bearer ${token.accessToken}")
                .build()
        }
    }

    private fun countPrior(response: Response): Int {
        var current: Response? = response
        var count = 0
        while (current != null && count < 4) {
            count += 1
            current = current.priorResponse
        }
        return count
    }

    private fun String.toRequestBody(mediaType: okhttp3.MediaType): okhttp3.RequestBody =
        okhttp3.RequestBody.create(mediaType, this)
}

/**
 * Rebuilds Retrofit whenever the user edits the backend base URL.
 */
class ApiProvider(private val session: SessionStore, val json: Json) {

    val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .callTimeout(60, TimeUnit.SECONDS)
        .addInterceptor(AuthInterceptor(session))
        .authenticator(TokenAuthenticator(session, json))
        .build()

    private var cachedBase: String? = null
    private var cachedApi: AionApi? = null

    fun api(): AionApi = synchronized(this) {
        val base = session.baseUrl
        val existing = cachedApi
        if (base.isNotBlank() && existing != null && cachedBase == base) return existing
        val retrofit = Retrofit.Builder()
            .baseUrl(if (base.isBlank()) "https://invalid.invalid/" else "$base/")
            .client(httpClient)
            .addConverterFactory(json.asConverterFactory(JSON_MEDIA_TYPE))
            .build()
        val created = retrofit.create(AionApi::class.java)
        cachedBase = base
        cachedApi = created
        created
    }

    fun invalidate() = synchronized(this) {
        cachedApi = null
        cachedBase = null
    }
}
