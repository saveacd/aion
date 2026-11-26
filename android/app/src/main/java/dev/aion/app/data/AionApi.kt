package dev.aion.app.data

import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

interface AionApi {

    @POST("api/v1/auth/login")
    suspend fun login(@Body body: LoginRequest): TokenResponse

    @POST("api/v1/auth/register")
    suspend fun register(@Body body: RegisterRequest): TokenResponse

    @POST("api/v1/auth/refresh")
    suspend fun refresh(@Body body: RefreshRequest): TokenResponse

    @POST("api/v1/auth/logout")
    suspend fun logout(@Body body: LogoutRequest): AckDto

    @GET("api/v1/auth/me")
    suspend fun me(): MeDto

    @GET("api/v1/account/overview")
    suspend fun overview(): AccountOverviewDto

    @GET("api/v1/account/positions")
    suspend fun positions(): PositionsResponse

    @GET("api/v1/market/symbols")
    suspend fun symbols(): SymbolsResponse

    @GET("api/v1/market/snapshot/{symbol}")
    suspend fun snapshot(@Path("symbol") symbol: String): TickerEnvelope

    @GET("api/v1/market/live")
    suspend fun livePrices(): LivePricesResponse

    @GET("api/v1/market/candles")
    suspend fun candles(
        @Query("symbol") symbol: String,
        @Query("interval") interval: String,
        @Query("limit") limit: Int = 200,
    ): CandleSeries

    @GET("api/v1/trading/orders")
    suspend fun orders(
        @Query("status") status: String? = null,
        @Query("limit") limit: Int = 100,
    ): OrdersResponse

    @GET("api/v1/trading/intents")
    suspend fun intents(@Query("limit") limit: Int = 50): IntentsResponse

    @POST("api/v1/trading/intents")
    suspend fun createIntent(@Body body: TradeIntentRequest): IntentResultDto

    @GET("api/v1/trading/fills")
    suspend fun fills(@Query("limit") limit: Int = 100): FillsResponse

    @GET("api/v1/risk/policy")
    suspend fun policy(): RiskPolicyResponse

    @PUT("api/v1/risk/policy")
    suspend fun updatePolicy(@Body body: JsonObject): RiskPolicyResponse

    @GET("api/v1/risk/decisions")
    suspend fun decisions(@Query("limit") limit: Int = 50): RiskDecisionsResponse

    @GET("api/v1/risk/events")
    suspend fun riskEvents(@Query("limit") limit: Int = 50): RiskEventsResponse

    @GET("api/v1/strategies")
    suspend fun strategies(): StrategiesResponse

    @GET("api/v1/strategies")
    suspend fun strategyReviewQueue(@Query("state") state: String = "SHADOW", @Query("limit") limit: Int = 100): StrategiesResponse

    @GET("api/v1/strategies/{strategy_id}/versions")
    suspend fun strategyVersions(@Path("strategy_id") strategyId: String): List<JsonElement>

    @POST("api/v1/strategies/{strategy_id}/state")
    suspend fun transitionStrategyState(@Path("strategy_id") strategyId: String, @Body body: JsonObject): JsonElement

    @GET("api/v1/agents/runs")
    suspend fun agentRuns(@Query("limit") limit: Int = 50): AgentRunsResponse

    @GET("api/v1/agents/runs/{run_id}")
    suspend fun agentRun(@Path("run_id") runId: String): AgentRunDto

    @POST("api/v1/agents/analyze")
    suspend fun analyze(@Body body: AnalyzeRequestDto): AnalyzeAcceptedDto

    @GET("api/v1/system/status")
    suspend fun systemStatus(): SystemStatusDto

    // Binance API credentials (per logged-in user).
    @GET("api/v1/settings/exchange")
    suspend fun exchangeSettings(): ExchangeSettingsResponse

    @PUT("api/v1/settings/exchange")
    suspend fun saveExchange(@Body body: ExchangeSaveRequest): ExchangeSaveResult

    @DELETE("api/v1/settings/exchange/{mode}")
    suspend fun deleteExchange(@Path("mode") mode: String): AckDto

    // The provider assigned to the current user by an administrator.
    @GET("api/v1/models/me")
    suspend fun myModelAssignment(): ModelAssignmentDto

    @POST("api/v1/system/controls")
    suspend fun control(@Body body: ControlRequest): ControlStateDto

    // Autopilot (全自动 AI 代理) and learning.
    @GET("api/v1/autopilot")
    suspend fun autopilotConfig(): AutopilotConfigDto

    @PUT("api/v1/autopilot")
    suspend fun updateAutopilot(@Body body: AutopilotUpdateRequest): AutopilotConfigDto

    @GET("api/v1/autopilot/status")
    suspend fun autopilotStatus(): AutopilotStatusDto

    @POST("api/v1/autopilot/positions/{id}/close")
    suspend fun closeLivePosition(@Path("id") id: String): JsonObject

    @GET("api/v1/learning/performance")
    suspend fun learningPerformance(): LearningPerformanceDto

    @GET("api/v1/learning/lessons")
    suspend fun learningLessons(): LessonListDto

    @POST("api/v1/learning/lessons/{lesson_id}/toggle")
    suspend fun toggleLesson(@Path("lesson_id") lessonId: String): LessonDto

    // Factor research (因子研究).
    @POST("api/v1/factors/research")
    suspend fun factorResearch(@Body body: FactorResearchRequest): FactorResearchReport

    @GET("api/v1/factors/library")
    suspend fun factorLibrary(@Query("symbol") symbol: String? = null): FactorLibraryResponse

    @POST("api/v1/factors/lifecycle/refresh")
    suspend fun refreshFactorLifecycle(@Query("symbol") symbol: String? = null): FactorLifecycleReportDto

    @GET("api/v1/factors/intelligence")
    suspend fun factorIntelligence(
        @Query("symbol") symbol: String,
        @Query("interval") interval: String,
    ): FactorIntelligenceDto

    @GET("api/v1/factors/research/tasks")
    suspend fun researchTasks(): List<JsonElement>

    @GET("api/v1/factors/research/tasks/{task_id}")
    suspend fun researchTask(@Path("task_id") taskId: String): JsonObject

    @POST("api/v1/factors/research/tasks/{task_id}/replay")
    suspend fun replayResearch(@Path("task_id") taskId: String): JsonObject


    @POST("api/v1/factors/research/tasks")
    suspend fun enqueueResearchTask(@Body body: JsonObject): JsonElement

    @GET("api/v1/research-lab/candidates")
    suspend fun researchCandidates(): List<JsonElement>

    @GET("api/v1/research-lab/compare")
    suspend fun researchCompare(): List<JsonElement>

    @GET("api/v1/research-lab/candidates/{candidate_id}/backtests")
    suspend fun candidateBacktests(@Path("candidate_id") candidateId: String): List<JsonElement>

    @GET("api/v1/research-lab/paper/sessions")
    suspend fun paperSessions(): List<JsonElement>

    @GET("api/v1/research-lab/paper/sessions/{session_id}/trades")
    suspend fun paperTrades(@Path("session_id") sessionId: String): List<JsonElement>

    @GET("api/v1/research-lab/ensembles")
    suspend fun researchEnsembles(@Query("symbol") symbol: String? = null): List<JsonElement>

    @GET("api/v1/research-lab/approvals")
    suspend fun researchApprovals(): List<JsonElement>

    @GET("api/v1/factors/research/tasks/{task_id}/logs")
    suspend fun researchTaskLogs(@Path("task_id") taskId: String): List<JsonElement>

    @GET("api/v1/factors/library/{factor_id}/validations")
    suspend fun factorValidations(@Path("factor_id") factorId: String): List<JsonElement>
}

/** Used when the PUT body needs flexible, partially-specified fields. */
typealias PolicyFields = Map<String, JsonElement>
