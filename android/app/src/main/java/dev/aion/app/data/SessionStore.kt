package dev.aion.app.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import dev.aion.app.BuildConfig
import kotlinx.coroutines.flow.MutableStateFlow
import java.net.URLEncoder

/**
 * Session material lives in EncryptedSharedPreferences. Only access/refresh
 * tokens and the username are stored; no password, no exchange
 * key, no model key is ever persisted on the device.
 */
class SessionStore(context: Context) {

    private val prefs: SharedPreferences = run {
        val masterKey = MasterKey.Builder(context, MasterKey.DEFAULT_MASTER_KEY_ALIAS)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            FILE_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        ).also { storage ->
            val previousUrl = storage.getString(LEGACY_BACKEND_URL, null)
            if (previousUrl != null) {
                val editor = storage.edit().remove(LEGACY_BACKEND_URL)
                if (previousUrl != BuildConfig.AION_BASE_URL) {
                    editor.remove(KEY_ACCESS).remove(KEY_REFRESH).remove(KEY_USERNAME)
                }
                editor.apply()
            }
        }
    }

    val sessionFlow = MutableStateFlow(accessToken.isNullOrBlank().not())

    /** Invoked when the refresh token is rejected, so the UI can drop to login. */
    var onExpired: (() -> Unit)? = null

    /** Fixed at build time so users never have to enter a server address. */
    val baseUrl: String get() = BuildConfig.AION_BASE_URL

    val accessToken: String? get() = prefs.getString(KEY_ACCESS, null)?.takeIf { it.isNotBlank() }
    val refreshToken: String? get() = prefs.getString(KEY_REFRESH, null)?.takeIf { it.isNotBlank() }
    val username: String? get() = prefs.getString(KEY_USERNAME, null)?.takeIf { it.isNotBlank() }
    val hasSession: Boolean get() = !accessToken.isNullOrBlank()

    fun saveTokens(token: TokenResponse) {
        val editor = prefs.edit().putString(KEY_ACCESS, token.accessToken)
        if (token.refreshToken.isNotBlank()) editor.putString(KEY_REFRESH, token.refreshToken)
        token.user?.username?.takeIf { it.isNotBlank() }?.let { editor.putString(KEY_USERNAME, it) }
        editor.apply()
        sessionFlow.value = true
    }

    fun rememberUsername(name: String) {
        prefs.edit().putString(KEY_USERNAME, name).apply()
    }

    fun clearSession() {
        prefs.edit()
            .remove(KEY_ACCESS)
            .remove(KEY_REFRESH)
            .apply()
        sessionFlow.value = false
    }

    fun expire() {
        if (!hasSession) return
        clearSession()
        onExpired?.invoke()
    }

    fun wsUrl(): String? {
        val token = accessToken ?: return null
        val base = baseUrl.trim().trimEnd('/')
        if (base.isBlank()) return null
        val wsBase = when {
            base.startsWith("https://") -> "wss://" + base.removePrefix("https://")
            base.startsWith("http://") -> "ws://" + base.removePrefix("http://")
            else -> "wss://$base"
        }
        return "$wsBase/api/v1/ws?token=" + URLEncoder.encode(token, "UTF-8")
    }

    companion object {
        private const val FILE_NAME = "aion_session"
        private const val LEGACY_BACKEND_URL = "backend_url"
        private const val KEY_ACCESS = "access_token"
        private const val KEY_REFRESH = "refresh_token"
        private const val KEY_USERNAME = "username"

        fun normalizeBase(raw: String): String {
            var value = raw.trim()
            if (value.isEmpty()) return ""
            if (!value.startsWith("http://") && !value.startsWith("https://")) {
                value = "https://$value"
            }
            value = value.trimEnd('/')
            if (value.endsWith("/api/v1")) {
                value = value.removeSuffix("/api/v1").trimEnd('/')
            }
            return value
        }
    }
}
