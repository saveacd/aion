package dev.aion.app.data

import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import retrofit2.HttpException
import java.io.IOException

/**
 * Thin, honest repository: every method returns live backend data or a failure.
 * No fallback/sample data is ever produced here.
 */
class AionRepository(
    private val api: ApiProvider,
    private val session: SessionStore,
    private val state: AppState,
    private val json: Json,
) {

    private val prettyJson = Json(json) { prettyPrint = true }

    // -- auth ---------------------------------------------------------------

    suspend fun login(
        username: String,
        password: String,
        totp: String?,
    ): ApiResult<Unit> {
        return when (
            val result = safe {
                api.api().login(
                    LoginRequest(
                        username = username.trim(),
                        password = password,
                        totpCode = totp?.trim()?.takeIf { it.isNotEmpty() },
                    ),
                )
            }
        ) {
            is ApiResult.Success -> {
                session.saveTokens(result.data)
                session.rememberUsername(username.trim())
                result.data.user?.let {
                    state.me.value = it
                    session.rememberUsername(it.username)
                }
                state.loggedIn.value = true
                ApiResult.Success(Unit)
            }
            is ApiResult.Failure -> result
        }
    }

    suspend fun register(username: String, password: String): ApiResult<Unit> {
        return when (val result = safe { api.api().register(RegisterRequest(username.trim(), password)) }) {
            is ApiResult.Success -> {
                session.saveTokens(result.data)
                session.rememberUsername(username.trim())
                result.data.user?.let {
                    state.me.value = it
                    session.rememberUsername(it.username)
                }
                state.loggedIn.value = true
                ApiResult.Success(Unit)
            }
            is ApiResult.Failure -> result
        }
    }

    suspend fun refreshMe(): ApiResult<MeDto> = safe { api.api().me() }.also {
        if (it is ApiResult.Success) state.me.value = it.data
    }

    suspend fun logout() {
        val token = session.refreshToken
        if (token != null) {
            safe { api.api().logout(LogoutRequest(refreshToken = token)) }
        }
        session.clearSession()
        state.loggedIn.value = false
        state.me.value = null
        state.system.value = null
    }

    // -- account ------------------------------------------------------------

    suspend fun overview(): ApiResult<AccountOverviewDto> = safe { api.api().overview() }

    suspend fun positions(): ApiResult<PositionsResponse> = safe { api.api().positions() }

    // -- market -------------------------------------------------------------

    suspend fun symbols(): ApiResult<SymbolsResponse> = safe { api.api().symbols() }

    suspend fun snapshot(symbol: String): ApiResult<TickerEnvelope> =
        safe { api.api().snapshot(symbol) }

    suspend fun livePrices(): ApiResult<LivePricesResponse> =
        safe { api.api().livePrices() }

    suspend fun candles(symbol: String, interval: String, limit: Int = 200): ApiResult<CandleSeries> =
        safe { api.api().candles(symbol, interval, limit) }

    // -- trading ------------------------------------------------------------

    suspend fun orders(limit: Int = 100): ApiResult<OrdersResponse> =
        safe { api.api().orders(limit = limit) }

    suspend fun intents(limit: Int = 50): ApiResult<IntentsResponse> =
        safe { api.api().intents(limit) }

    suspend fun submitIntent(request: TradeIntentRequest): ApiResult<IntentResultDto> =
        safe { api.api().createIntent(request) }

    suspend fun fills(limit: Int = 100): ApiResult<FillsResponse> =
        safe { api.api().fills(limit) }

    // -- risk ---------------------------------------------------------------

    suspend fun policy(): ApiResult<RiskPolicyResponse> = safe { api.api().policy() }

    suspend fun updatePolicy(fields: Map<String, JsonElement>): ApiResult<RiskPolicyResponse> =
        safe { api.api().updatePolicy(JsonObject(fields)) }

    suspend fun decisions(limit: Int = 50): ApiResult<RiskDecisionsResponse> =
        safe { api.api().decisions(limit) }

    suspend fun riskEvents(limit: Int = 50): ApiResult<RiskEventsResponse> =
        safe { api.api().riskEvents(limit) }

    // -- strategies / agents -----------------------------------------------

    suspend fun strategies(): ApiResult<StrategiesResponse> = safe { api.api().strategies() }

    suspend fun agentRuns(limit: Int = 50): ApiResult<AgentRunsResponse> =
        safe { api.api().agentRuns(limit) }

    suspend fun agentRun(runId: String): ApiResult<AgentRunDto> =
        safe { api.api().agentRun(runId) }

    suspend fun analyze(symbol: String, interval: String = "15m"): ApiResult<AnalyzeAcceptedDto> =
        safe { api.api().analyze(AnalyzeRequestDto(symbol = symbol, interval = interval)) }

    // -- system -------------------------------------------------------------

    suspend fun systemStatus(): ApiResult<SystemStatusDto> {
        val result = safe { api.api().systemStatus() }
        if (result is ApiResult.Success) state.system.value = result.data
        return result
    }

    // -- exchange / model assignment ---------------------------------------

    suspend fun exchangeSettings(): ApiResult<ExchangeSettingsResponse> =
        safe { api.api().exchangeSettings() }

    suspend fun saveExchange(
        mode: String,
        apiKey: String,
        apiSecret: String,
        verify: Boolean = true,
    ): ApiResult<ExchangeSaveResult> =
        safe {
            api.api().saveExchange(
                ExchangeSaveRequest(mode = mode, apiKey = apiKey, apiSecret = apiSecret, verify = verify),
            )
        }

    suspend fun deleteExchange(mode: String): ApiResult<AckDto> =
        safe { api.api().deleteExchange(mode) }

    suspend fun myModelAssignment(): ApiResult<ModelAssignmentDto> =
        safe { api.api().myModelAssignment() }

    suspend fun autopilotConfig(): ApiResult<AutopilotConfigDto> =
        safe { api.api().autopilotConfig() }

    suspend fun updateAutopilot(body: AutopilotUpdateRequest): ApiResult<AutopilotConfigDto> =
        safe { api.api().updateAutopilot(body) }

    suspend fun autopilotStatus(): ApiResult<AutopilotStatusDto> =
        safe { api.api().autopilotStatus() }

    suspend fun closeLivePosition(id: String): ApiResult<Unit> =
        when (val result = safe { api.api().closeLivePosition(id) }) {
            is ApiResult.Success -> ApiResult.Success(Unit)
            is ApiResult.Failure -> result
        }

    suspend fun learningPerformance(): ApiResult<LearningPerformanceDto> =
        safe { api.api().learningPerformance() }

    suspend fun learningLessons(): ApiResult<LessonListDto> =
        safe { api.api().learningLessons() }

    suspend fun toggleLesson(id: String): ApiResult<LessonDto> =
        safe { api.api().toggleLesson(id) }

    suspend fun factorResearch(symbol: String, interval: String, horizonBars: Int): ApiResult<FactorResearchReport> =
        safe { api.api().factorResearch(FactorResearchRequest(symbol, interval, horizonBars)) }

    suspend fun factorLibrary(symbol: String? = null): ApiResult<FactorLibraryResponse> =
        safe { api.api().factorLibrary(symbol?.takeIf { it.isNotBlank() }) }

    suspend fun refreshFactorLifecycle(symbol: String?): ApiResult<FactorLifecycleReportDto> =
        safe { api.api().refreshFactorLifecycle(symbol?.takeIf { it.isNotBlank() }) }

    suspend fun factorIntelligence(symbol: String, interval: String): ApiResult<FactorIntelligenceDto> =
        safe { api.api().factorIntelligence(symbol, interval) }

    suspend fun setControl(action: String, enabled: Boolean, reason: String?): ApiResult<ControlStateDto> {
        val result = safe { api.api().control(ControlRequest(action, enabled, reason)) }
        if (result is ApiResult.Success) {
            val current = state.system.value
            if (current != null) {
                state.system.value = current.copy(controls = result.data)
            }
        }
        return result
    }

    // -- plumbing -----------------------------------------------------------

    private suspend fun <T> safe(block: suspend () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: CancellationException) {
        throw e
    } catch (e: HttpException) {
        val failure = failureFromHttp(e)
        if (e.code() == 401) session.expire()
        failure
    } catch (e: IOException) {
        ApiResult.Failure(
            code = "NETWORK",
            message = "网络连接失败，请检查网络后重试。",
        )
    } catch (e: Exception) {
        ApiResult.Failure(
            code = "CLIENT",
            message = "请求处理失败：${e.message ?: e.javaClass.simpleName}",
        )
    }

    private fun failureFromHttp(e: HttpException): ApiResult.Failure {
        val raw = try {
            e.response()?.errorBody()?.string()
        } catch (ignored: Exception) {
            null
        }
        val parsed = parseError(raw)
        return ApiResult.Failure(
            httpStatus = e.code(),
            code = parsed.code,
            message = parsed.message.ifBlank { "HTTP ${e.code()}" },
            requestId = parsed.requestId,
            details = parsed.details,
        )
    }

    private fun parseError(raw: String?): ParsedError {
        if (raw.isNullOrBlank()) return ParsedError(null, "", null, null)
        return try {
            val root = json.parseToJsonElement(raw)
            val obj = root as? JsonObject ?: return ParsedError(null, raw.take(400), null, null)
            val errorObj = (obj["error"] as? JsonObject) ?: obj
            val code = (errorObj["code"] as? JsonPrimitive)?.content
            val message = (errorObj["message"] as? JsonPrimitive)?.content.orEmpty()
            val requestId = (errorObj["request_id"] as? JsonPrimitive)?.content
            val detailsElement = errorObj["details"]?.takeIf { it !is JsonNull }
                ?: obj["reason_codes"]?.takeIf { it !is JsonNull }
                ?: errorObj["reason_codes"]?.takeIf { it !is JsonNull }
            val details = detailsElement?.let { prettyJson.encodeToString(JsonElement.serializer(), it) }
            ParsedError(code, message, requestId, details)
        } catch (e: Exception) {
            ParsedError(null, raw.take(400), null, null)
        }
    }

    private data class ParsedError(
        val code: String?,
        val message: String,
        val requestId: String?,
        val details: String?,
    )
}
