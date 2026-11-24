package dev.aion.app

import android.content.Context
import dev.aion.app.data.AionRepository
import dev.aion.app.data.ApiProvider
import dev.aion.app.data.AppState
import dev.aion.app.data.RealtimeClient
import dev.aion.app.data.SessionStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.serialization.json.Json

/**
 * Hand-written dependency container (no Hilt/Koin on purpose, to keep the build
 * small and predictable).
 */
class AppContainer(context: Context) {

    val session = SessionStore(context)
    val appState = AppState()

    val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        coerceInputValues = true
        encodeDefaults = true
        explicitNulls = false
    }

    val api = ApiProvider(session, json)
    val repository = AionRepository(api, session, appState, json)

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    val realtime = RealtimeClient(session, api.httpClient, appState, json, scope)

    init {
        appState.loggedIn.value = session.hasSession
        session.onExpired = {
            appState.loggedIn.value = false
            appState.me.value = null
            realtime.disconnect()
        }
    }
}
