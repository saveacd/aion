package dev.aion.app.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class ConnectionState { CONNECTING, ONLINE, RECONNECTING, OFFLINE }

class AppState {

    val loggedIn = MutableStateFlow(false)
    val me = MutableStateFlow<MeDto?>(null)
    val system = MutableStateFlow<SystemStatusDto?>(null)
    val connection = MutableStateFlow(ConnectionState.OFFLINE)

    private val _refreshSignal = MutableStateFlow(0)
    val refreshSignal = _refreshSignal.asStateFlow()

    fun requestRefresh() {
        _refreshSignal.value += 1
    }

    val tradingMode: String? get() = system.value?.mode
}
