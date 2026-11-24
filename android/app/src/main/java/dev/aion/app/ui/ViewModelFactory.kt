package dev.aion.app.ui

import androidx.compose.runtime.Composable
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import dev.aion.app.AppContainer
import dev.aion.app.data.ApiResult
import dev.aion.app.data.Loadable
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.MutableStateFlow

class AionViewModelFactory(
    private val container: AppContainer,
    private val builder: (AppContainer) -> ViewModel,
) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T = builder(container) as T
}

@Composable
inline fun <reified VM : ViewModel> aionViewModel(
    container: AppContainer,
    noinline builder: (AppContainer) -> VM,
): VM = viewModel<VM>(factory = AionViewModelFactory(container, builder))

/**
 * Small load helper shared by all screens. Keeps previous data on refresh
 * failures so stale values stay visible together with an error banner.
 */
abstract class BaseViewModel(protected val container: AppContainer) : ViewModel() {

    protected fun <T> load(
        state: MutableStateFlow<Loadable<T>>,
        block: suspend () -> ApiResult<T>,
    ): Job {
        val previous = state.value
        state.value = previous.copy(loading = true, error = null, requestId = null)
        return viewModelScope.launch {
            val result = block()
            state.value = when (result) {
                is ApiResult.Success -> Loadable(data = result.data)
                is ApiResult.Failure -> previous.copy(
                    loading = false,
                    error = result.userMessage(),
                    requestId = result.requestId,
                )
            }
        }
    }
}
