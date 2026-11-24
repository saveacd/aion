package dev.aion.app.ui

import androidx.lifecycle.viewModelScope
import dev.aion.app.AppContainer
import dev.aion.app.data.ApiResult
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

data class LoginUiState(
    val submitting: Boolean = false,
    val error: String? = null,
    val requestId: String? = null,
)

class SessionViewModel(container: AppContainer) : BaseViewModel(container) {

    val me = container.appState.me
    val system = container.appState.system
    val connection = container.appState.connection

    val baseUrl = MutableStateFlow(container.session.baseUrl)
    val loginState = MutableStateFlow(LoginUiState())
    val loggingOut = MutableStateFlow(false)

    fun bootstrap() {
        viewModelScope.launch {
            container.repository.refreshMe()
            container.repository.systemStatus()
        }
    }

    fun login(username: String, password: String, totp: String) {
        if (loginState.value.submitting) return
        if (username.isBlank() || password.isBlank()) {
            loginState.value = LoginUiState(error = "请输入用户名和密码")
            return
        }
        loginState.value = LoginUiState(submitting = true)
        viewModelScope.launch {
            when (val result = container.repository.login(username, password, totp)) {
                is ApiResult.Success -> {
                    loginState.value = LoginUiState()
                    container.realtime.connect()
                }
                is ApiResult.Failure -> {
                    loginState.value = LoginUiState(
                        error = result.userMessage(),
                        requestId = result.requestId,
                    )
                }
            }
        }
    }

    fun register(username: String, password: String) {
        if (loginState.value.submitting) return
        if (username.isBlank() || password.isBlank()) {
            loginState.value = LoginUiState(error = "请输入用户名和密码")
            return
        }
        loginState.value = LoginUiState(submitting = true)
        viewModelScope.launch {
            when (val result = container.repository.register(username, password)) {
                is ApiResult.Success -> {
                    loginState.value = LoginUiState()
                    container.realtime.connect()
                }
                is ApiResult.Failure -> {
                    loginState.value = LoginUiState(
                        error = result.userMessage(),
                        requestId = result.requestId,
                    )
                }
            }
        }
    }

    fun clearLoginError() {
        loginState.value = LoginUiState()
    }

    fun logout() {
        if (loggingOut.value) return
        loggingOut.value = true
        viewModelScope.launch {
            container.repository.logout()
            container.realtime.disconnect()
            loggingOut.value = false
        }
    }

}
