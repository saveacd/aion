package dev.aion.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

@Serializable
data class LoginRequest(
    val username: String,
    val password: String,
    @SerialName("totp_code") val totpCode: String? = null,
    @SerialName("device_name") val deviceName: String = "AION Android",
)

@Serializable
data class RegisterRequest(
    val username: String,
    val password: String,
    @SerialName("device_name") val deviceName: String = "AION Android",
)

@Serializable
data class RefreshRequest(@SerialName("refresh_token") val refreshToken: String)

@Serializable
data class LogoutRequest(
    @SerialName("refresh_token") val refreshToken: String? = null,
    @SerialName("all_devices") val allDevices: Boolean = false,
)

@Serializable
data class TokenResponse(
    @SerialName("access_token") val accessToken: String = "",
    @SerialName("refresh_token") val refreshToken: String = "",
    @SerialName("token_type") val tokenType: String = "Bearer",
    @SerialName("expires_in") val expiresIn: Long? = null,
    @SerialName("access_expires_at") val accessExpiresAt: String? = null,
    @SerialName("refresh_expires_at") val refreshExpiresAt: String? = null,
    @SerialName("session_id") val sessionId: String? = null,
    val user: MeDto? = null,
    @SerialName("totp_enabled") val totpEnabled: Boolean = false,
    @SerialName("must_change_password") val mustChangePassword: Boolean = false,
)

@Serializable
data class MeDto(
    val username: String = "",
    @SerialName("is_admin") val isAdmin: Boolean = false,
    @SerialName("totp_enabled") val totpEnabled: Boolean = false,
    @SerialName("last_login_at") val lastLoginAt: String? = null,
    val role: String = "user",
    val features: List<String> = emptyList(),
)

// ---------------------------------------------------------------------------
// Binance API credentials / model assignment
// ---------------------------------------------------------------------------

@Serializable
data class ExchangeSettingDto(
    val mode: String = "",
    val label: String? = null,
    @SerialName("has_api_key") val hasApiKey: Boolean = false,
    @SerialName("has_api_secret") val hasApiSecret: Boolean = false,
    @SerialName("permissions_checked") val permissionsChecked: Boolean = false,
    @SerialName("can_trade") val canTrade: Boolean? = null,
    @SerialName("can_withdraw") val canWithdraw: Boolean? = null,
    @SerialName("last_checked_at") val lastCheckedAt: String? = null,
    @SerialName("is_active") val isActive: Boolean = true,
)

@Serializable
data class ExchangeSettingsResponse(
    val items: List<ExchangeSettingDto> = emptyList(),
    val configured: Map<String, Boolean> = emptyMap(),
)

@Serializable
data class ExchangeSaveRequest(
    val mode: String,
    @SerialName("api_key") val apiKey: String,
    @SerialName("api_secret") val apiSecret: String,
    val verify: Boolean = true,
)

@Serializable
data class ExchangeSaveResult(
    val mode: String = "",
    val saved: Boolean = false,
    val verified: Boolean = false,
    val message: String = "",
    val item: ExchangeSettingDto? = null,
)

@Serializable
data class ModelAssignmentDto(
    @SerialName("user_id") val userId: String = "",
    val username: String? = null,
    @SerialName("provider_id") val providerId: String? = null,
    @SerialName("provider_name") val providerName: String? = null,
    @SerialName("provider_kind") val providerKind: String? = null,
    @SerialName("light_model") val lightModel: String? = null,
    @SerialName("heavy_model") val heavyModel: String? = null,
    @SerialName("daily_budget_usd") val dailyBudgetUsd: JsonElement? = null,
    val enabled: Boolean = false,
    val configured: Boolean = false,
) {
    val budgetText: String? get() = jsonScalarText(dailyBudgetUsd)
}

@Serializable
data class AckDto(val ok: Boolean = true, val message: String? = null)

// ---------------------------------------------------------------------------
// Account
// ---------------------------------------------------------------------------

@Serializable
data class AccountSnapshotDto(
    val mode: String? = null,
    @SerialName("total_wallet_balance") val totalWalletBalance: String? = null,
    @SerialName("total_unrealized_pnl") val totalUnrealizedPnl: String? = null,
    @SerialName("total_margin_balance") val totalMarginBalance: String? = null,
    @SerialName("available_balance") val availableBalance: String? = null,
    @SerialName("total_initial_margin") val totalInitialMargin: String? = null,
    @SerialName("total_maint_margin") val totalMaintMargin: String? = null,
    @SerialName("margin_ratio") val marginRatio: String? = null,
    @SerialName("drawdown_pct") val drawdownPct: String? = null,
    @SerialName("daily_realized_pnl") val dailyRealizedPnl: String? = null,
    @SerialName("open_positions") val openPositions: Int = 0,
    @SerialName("captured_at") val capturedAt: String? = null,
    val stale: Boolean = false,
    // Tolerant aliases for alternative API revisions.
    val equity: String? = null,
    @SerialName("total_equity") val totalEquity: String? = null,
    @SerialName("unrealized_pnl") val unrealizedPnl: String? = null,
    @SerialName("change_pct") val changePct: String? = null,
) {
    val equityValue: String? get() = totalWalletBalance ?: equity ?: totalEquity
    val unrealizedValue: String? get() = totalUnrealizedPnl ?: unrealizedPnl
}

@Serializable
data class AccountOverviewDto(
    @SerialName("has_data") val hasData: Boolean = false,
    val mode: String? = null,
    val account: AccountSnapshotDto? = null,
    val snapshot: AccountSnapshotDto? = null,
    val positions: List<PositionDto> = emptyList(),
    val stale: Boolean = false,
) {
    val snapshotValue: AccountSnapshotDto? get() = account ?: snapshot
}

@Serializable
data class PositionDto(
    val symbol: String = "",
    @SerialName("position_side") val positionSide: String = "",
    val quantity: String? = null,
    @SerialName("entry_price") val entryPrice: String? = null,
    @SerialName("mark_price") val markPrice: String? = null,
    @SerialName("unrealized_pnl") val unrealizedPnl: String? = null,
    @SerialName("unrealized_pnl_pct") val unrealizedPnlPct: String? = null,
    @SerialName("liquidation_price") val liquidationPrice: String? = null,
    val leverage: Int = 0,
    @SerialName("margin_mode") val marginMode: String? = null,
    @SerialName("notional_usd") val notionalUsd: String? = null,
    @SerialName("has_stop_loss") val hasStopLoss: Boolean = false,
    @SerialName("has_take_profit") val hasTakeProfit: Boolean = false,
    @SerialName("stop_loss_price") val stopLossPrice: String? = null,
    @SerialName("take_profit_price") val takeProfitPrice: String? = null,
    @SerialName("protection_verified") val protectionVerified: Boolean = false,
)

@Serializable
data class PositionsResponse(
    @SerialName("has_data") val hasData: Boolean = false,
    val mode: String? = null,
    val items: List<PositionDto> = emptyList(),
    val positions: List<PositionDto> = emptyList(),
    val stale: Boolean = false,
) {
    val all: List<PositionDto> get() = if (items.isNotEmpty()) items else positions
}

// ---------------------------------------------------------------------------
// Market
// ---------------------------------------------------------------------------

@Serializable
data class SymbolDto(
    val symbol: String = "",
    @SerialName("base_asset") val baseAsset: String? = null,
    @SerialName("quote_asset") val quoteAsset: String? = null,
    @SerialName("contract_type") val contractType: String? = null,
    val status: String? = null,
    @SerialName("price_precision") val pricePrecision: Int? = null,
    @SerialName("quantity_precision") val quantityPrecision: Int? = null,
    @SerialName("tick_size") val tickSize: String? = null,
    @SerialName("step_size") val stepSize: String? = null,
    @SerialName("min_qty") val minQty: String? = null,
    @SerialName("max_qty") val maxQty: String? = null,
    @SerialName("min_notional") val minNotional: String? = null,
    @SerialName("max_leverage") val maxLeverage: Int? = null,
    @SerialName("is_allowed") val isAllowed: Boolean = true,
    val price: String? = null,
)

@Serializable
data class SymbolsResponse(val items: List<SymbolDto> = emptyList(), val total: Int = 0)

@Serializable
data class TickerDto(
    val symbol: String = "",
    @SerialName("last_price") val lastPrice: String? = null,
    @SerialName("mark_price") val markPrice: String? = null,
    @SerialName("index_price") val indexPrice: String? = null,
    @SerialName("price_change_pct_24h") val priceChangePct24h: String? = null,
    @SerialName("change_pct") val changePct: String? = null,
    @SerialName("high_24h") val high24h: String? = null,
    @SerialName("low_24h") val low24h: String? = null,
    @SerialName("volume_24h") val volume24h: String? = null,
    @SerialName("quote_volume_24h") val quoteVolume24h: String? = null,
    @SerialName("spread_bps") val spreadBps: String? = null,
    @SerialName("source_ts") val sourceTs: String? = null,
    @SerialName("captured_at") val capturedAt: String? = null,
    val stale: Boolean = false,
) {
    val changeValue: String? get() = priceChangePct24h ?: changePct
}

@Serializable
data class TickerEnvelope(
    @SerialName("has_data") val hasData: Boolean = true,
    val symbol: String? = null,
    val ticker: TickerDto? = null,
    @SerialName("last_price") val lastPrice: String? = null,
    @SerialName("change_pct") val changePct: String? = null,
    @SerialName("captured_at") val capturedAt: String? = null,
) {
    val priceValue: String? get() = ticker?.lastPrice ?: lastPrice
    val changeValue: String? get() = ticker?.changeValue ?: changePct
    val markValue: String? get() = ticker?.markPrice
    val timeValue: String? get() = ticker?.capturedAt ?: ticker?.sourceTs ?: capturedAt
    val isStale: Boolean get() = ticker?.stale == true
}

@Serializable
data class CandleDto(
    @SerialName("open_time") val openTime: String? = null,
    @SerialName("close_time") val closeTime: String? = null,
    val open: String? = null,
    val high: String? = null,
    val low: String? = null,
    val close: String? = null,
    val volume: String? = null,
    @SerialName("quote_volume") val quoteVolume: String? = null,
    @SerialName("is_final") val isFinal: Boolean = true,
)

@Serializable
data class CandleSeries(
    val symbol: String = "",
    val interval: String = "",
    val candles: List<CandleDto> = emptyList(),
    val items: List<CandleDto> = emptyList(),
    val source: String? = null,
    val stale: Boolean = false,
) {
    val data: List<CandleDto> get() = if (candles.isNotEmpty()) candles else items
}

/** 1-second ticker from the backend's live WebSocket feed. */
@Serializable
data class LivePriceDto(
    val symbol: String = "",
    val price: String = "0",
    @SerialName("change_pct_24h") val changePct24h: String? = null,
    @SerialName("captured_at") val capturedAt: String? = null,
    @SerialName("age_ms") val ageMs: Long? = null,
)

@Serializable
data class LivePricesResponse(
    @SerialName("server_time") val serverTime: String? = null,
    val items: List<LivePriceDto> = emptyList(),
)

// ---------------------------------------------------------------------------
// Trading
// ---------------------------------------------------------------------------

@Serializable
data class TradeIntentRequest(
    val symbol: String,
    val side: String,
    @SerialName("position_side") val positionSide: String,
    @SerialName("order_type") val orderType: String = "MARKET",
    val quantity: String? = null,
    @SerialName("notional_usd") val notionalUsd: String? = null,
    @SerialName("requested_leverage") val requestedLeverage: Int = 1,
    @SerialName("entry_price") val entryPrice: String? = null,
    @SerialName("stop_loss") val stopLoss: String? = null,
    @SerialName("take_profit") val takeProfit: String? = null,
    @SerialName("time_in_force") val timeInForce: String = "GTC",
    val reason: String? = null,
    @SerialName("client_intent_key") val clientIntentKey: String? = null,
)

@Serializable
data class IntentDto(
    val id: String? = null,
    @SerialName("intent_key") val intentKey: String? = null,
    @SerialName("run_id") val runId: String? = null,
    val symbol: String = "",
    val side: String = "",
    @SerialName("position_side") val positionSide: String = "",
    @SerialName("intent_type") val intentType: String? = null,
    @SerialName("order_type") val orderType: String? = null,
    val quantity: String? = null,
    @SerialName("notional_usd") val notionalUsd: String? = null,
    @SerialName("requested_leverage") val requestedLeverage: Int = 0,
    @SerialName("entry_price") val entryPrice: String? = null,
    @SerialName("stop_loss") val stopLoss: String? = null,
    @SerialName("take_profit") val takeProfit: String? = null,
    val status: String = "",
    val mode: String? = null,
    val reason: String? = null,
    @SerialName("rejection_code") val rejectionCode: String? = null,
    @SerialName("rejection_detail") val rejectionDetail: JsonElement? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("confidence") val confidence: JsonElement? = null,
)

@Serializable
data class RiskDecisionDto(
    val id: String? = null,
    @SerialName("intent_id") val intentId: String? = null,
    val symbol: String = "",
    val approved: Boolean = false,
    @SerialName("reason_codes") val reasonCodes: List<String> = emptyList(),
    @SerialName("checked_limits") val checkedLimits: JsonElement? = null,
    @SerialName("risk_snapshot") val riskSnapshot: JsonElement? = null,
    val mode: String? = null,
    @SerialName("decided_at") val decidedAt: String? = null,
    @SerialName("latency_ms") val latencyMs: Int? = null,
)

@Serializable
data class IntentResultDto(
    val intent: IntentDto? = null,
    val risk: RiskDecisionDto? = null,
    val orders: List<OrderDto> = emptyList(),
    val accepted: Boolean = false,
    @SerialName("intent_id") val intentId: String? = null,
    val status: String? = null,
) {
    val effectiveId: String? get() = intent?.id ?: intentId
    val effectiveStatus: String? get() = intent?.status ?: status
}

@Serializable
data class OrderDto(
    val id: String? = null,
    @SerialName("client_order_id") val clientOrderId: String? = null,
    @SerialName("exchange_order_id") val exchangeOrderId: String? = null,
    val symbol: String = "",
    val side: String = "",
    @SerialName("position_side") val positionSide: String? = null,
    @SerialName("order_type") val orderType: String = "",
    val purpose: String? = null,
    @SerialName("reduce_only") val reduceOnly: Boolean = false,
    val quantity: String? = null,
    val price: String? = null,
    @SerialName("stop_price") val stopPrice: String? = null,
    @SerialName("filled_quantity") val filledQuantity: String? = null,
    @SerialName("avg_fill_price") val avgFillPrice: String? = null,
    val status: String = "",
    val mode: String? = null,
    @SerialName("submit_attempts") val submitAttempts: Int = 0,
    @SerialName("error_code") val errorCode: String? = null,
    @SerialName("error_message") val errorMessage: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("submitted_at") val submittedAt: String? = null,
    @SerialName("terminal_at") val terminalAt: String? = null,
)

@Serializable
data class FillDto(
    val id: String? = null,
    @SerialName("exchange_trade_id") val exchangeTradeId: String? = null,
    val symbol: String = "",
    val side: String = "",
    val price: String? = null,
    val quantity: String? = null,
    @SerialName("quote_quantity") val quoteQuantity: String? = null,
    val commission: String? = null,
    @SerialName("commission_asset") val commissionAsset: String? = null,
    @SerialName("realized_pnl") val realizedPnl: String? = null,
    @SerialName("is_maker") val isMaker: Boolean = false,
    @SerialName("traded_at") val tradedAt: String? = null,
)

@Serializable
data class OrdersResponse(
    val items: List<OrderDto> = emptyList(),
    val total: Int = 0,
    val limit: Int = 0,
    val offset: Int = 0,
)

@Serializable
data class FillsResponse(
    val items: List<FillDto> = emptyList(),
    val total: Int = 0,
    val limit: Int = 0,
    val offset: Int = 0,
)

@Serializable
data class IntentsResponse(
    val items: List<IntentDto> = emptyList(),
    val total: Int = 0,
    val limit: Int = 0,
    val offset: Int = 0,
)

// ---------------------------------------------------------------------------
// Risk
// ---------------------------------------------------------------------------

@Serializable
data class RiskPolicyDto(
    val id: Int? = null,
    val name: String = "",
    @SerialName("is_active") val isActive: Boolean = true,
    @SerialName("max_leverage") val maxLeverage: Int? = null,
    @SerialName("max_position_notional_usd") val maxPositionNotionalUsd: String? = null,
    @SerialName("max_total_exposure_usd") val maxTotalExposureUsd: String? = null,
    @SerialName("max_single_trade_loss_usd") val maxSingleTradeLossUsd: String? = null,
    @SerialName("daily_loss_limit_usd") val dailyLossLimitUsd: String? = null,
    @SerialName("max_drawdown_pct") val maxDrawdownPct: String? = null,
    @SerialName("max_consecutive_losses") val maxConsecutiveLosses: Int? = null,
    @SerialName("max_open_positions") val maxOpenPositions: Int? = null,
    @SerialName("max_directional_concentration_pct") val maxDirectionalConcentrationPct: String? = null,
    @SerialName("min_margin_ratio") val minMarginRatio: String? = null,
    @SerialName("required_stop_loss") val requiredStopLoss: Boolean = false,
    @SerialName("max_spread_bps") val maxSpreadBps: String? = null,
    @SerialName("min_confidence") val minConfidence: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

@Serializable
data class RiskPolicyResponse(
    @SerialName("has_data") val hasData: Boolean = false,
    val policy: RiskPolicyDto? = null,
    @SerialName("absolute_limits") val absoluteLimits: Map<String, JsonElement> = emptyMap(),
)

@Serializable
data class RiskEventDto(
    val id: String? = null,
    val severity: String = "INFO",
    val code: String = "",
    val message: String = "",
    val symbol: String? = null,
    val context: JsonElement? = null,
    @SerialName("acknowledged_at") val acknowledgedAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
)

@Serializable
data class RiskDecisionsResponse(
    val items: List<RiskDecisionDto> = emptyList(),
    val total: Int = 0,
    val limit: Int = 0,
    val offset: Int = 0,
)

@Serializable
data class RiskEventsResponse(
    val items: List<RiskEventDto> = emptyList(),
    val total: Int = 0,
    val limit: Int = 0,
    val offset: Int = 0,
)

// ---------------------------------------------------------------------------
// Strategies / agents
// ---------------------------------------------------------------------------

@Serializable
data class StrategyDto(
    val id: String? = null,
    val name: String = "",
    val description: String? = null,
    val state: String = "",
    val symbols: List<String> = emptyList(),
    val interval: String = "",
    @SerialName("active_version_id") val activeVersionId: String? = null,
    @SerialName("created_by") val createdBy: String? = null,
    val notes: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    val version: Int? = null,
    @SerialName("backtest_summary") val backtestSummary: JsonElement? = null,
)

@Serializable
data class StrategiesResponse(
    val items: List<StrategyDto> = emptyList(),
    val total: Int = 0,
    val limit: Int = 0,
    val offset: Int = 0,
)

@Serializable
data class AgentRunDto(
    val id: String? = null,
    @SerialName("run_id") val runId: String? = null,
    val agent: String? = null,
    @SerialName("agent_name") val agentName: String? = null,
    val type: String? = null,
    @SerialName("run_type") val runType: String? = null,
    val symbol: String? = null,
    val interval: String? = null,
    val status: String? = null,
    val summary: String? = null,
    val verdict: String? = null,
    val trigger: String? = null,
    val mode: String? = null,
    val confidence: JsonElement? = null,
    val warnings: List<JsonElement>? = null,
    val warning: String? = null,
    @SerialName("suggested_action") val suggestedAction: String? = null,
    @SerialName("recommended_action") val recommendedAction: String? = null,
    val recommendation: String? = null,
    val action: String? = null,
    val provider: String? = null,
    @SerialName("model_name") val modelName: String? = null,
    @SerialName("duration_ms") val durationMs: Long? = null,
    @SerialName("total_cost_usd") val totalCostUsd: String? = null,
    val messages: List<AgentMessageDto>? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("finished_at") val finishedAt: String? = null,
    val error: String? = null,
) {
    val title: String
        get() = agentName ?: agent ?: symbol?.let { "$it 分析" } ?: runType ?: type ?: "Agent Run"
    val actionText: String?
        get() = suggestedAction ?: recommendedAction ?: recommendation ?: action ?: verdict
    val confidenceText: String? get() = jsonScalarText(confidence)
    val identifier: String? get() = id ?: runId
}

@Serializable
data class AgentMessageDto(
    val seq: Int = 0,
    @SerialName("agent_name") val agentName: String? = null,
    val role: String? = null,
    val status: String? = null,
    val summary: String? = null,
    val warnings: List<JsonElement>? = null,
    val confidence: JsonElement? = null,
    @SerialName("latency_ms") val latencyMs: Long? = null,
    @SerialName("created_at") val createdAt: String? = null,
) {
    val confidenceText: String? get() = jsonScalarText(confidence)
}

@Serializable
data class AnalyzeRequestDto(
    val symbol: String,
    val interval: String = "15m",
)

@Serializable
data class AnalyzeAcceptedDto(
    @SerialName("run_id") val runId: String,
    val status: String? = null,
    val symbol: String? = null,
    val interval: String? = null,
    val note: String? = null,
)

@Serializable
data class AgentRunsResponse(
    val items: List<AgentRunDto> = emptyList(),
    val total: Int = 0,
    val limit: Int = 0,
    val offset: Int = 0,
)

fun AgentRunDto.warningTexts(): List<String> =
    (warnings.orEmpty().mapNotNull { jsonScalarText(it) } + listOfNotNull(warning))
        .filter { it.isNotBlank() }

fun jsonScalarText(element: JsonElement?): String? = when (element) {
    null, JsonNull -> null
    is JsonPrimitive -> element.content.takeIf { it.isNotBlank() }
    else -> element.toString()
}

// ---------------------------------------------------------------------------
// System
// ---------------------------------------------------------------------------

@Serializable
data class ExchangeStatusDto(
    val mode: String? = null,
    @SerialName("credentials_configured") val credentialsConfigured: Boolean = false,
    @SerialName("has_api_key") val hasApiKey: Boolean = false,
    @SerialName("has_api_secret") val hasApiSecret: Boolean = false,
    @SerialName("account_row_active") val accountRowActive: Boolean = false,
    @SerialName("permissions_checked") val permissionsChecked: Boolean = false,
    val configured: Boolean? = null,
    val connected: Boolean? = null,
)

@Serializable
data class ControlStateDto(
    @SerialName("stop_new_trades") val stopNewTrades: Boolean = false,
    @SerialName("pause_ai") val pauseAi: Boolean = false,
    @SerialName("emergency_stop") val emergencyStop: Boolean = false,
    @SerialName("emergency_reason") val emergencyReason: String? = null,
    @SerialName("updated_by") val updatedBy: String? = null,
    @SerialName("engaged_at") val engagedAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

@Serializable
data class SystemStatusDto(
    @SerialName("app_name") val appName: String? = null,
    val version: String? = null,
    val environment: String? = null,
    @SerialName("debug") val debug: Boolean = false,
    @SerialName("trading_mode") val tradingMode: String? = null,
    @SerialName("mainnet_unlocked") val mainnetUnlocked: Boolean = false,
    @SerialName("mainnet_enabled") val mainnetEnabled: Boolean? = null,
    @SerialName("database_ok") val databaseOk: Boolean? = null,
    @SerialName("redis_ok") val redisOk: Boolean? = null,
    val database: Boolean? = null,
    val redis: Boolean? = null,
    val backend: String? = null,
    @SerialName("exchange_configured") val exchangeConfigured: Boolean? = null,
    val exchange: List<ExchangeStatusDto> = emptyList(),
    val controls: ControlStateDto? = null,
    @SerialName("stop_new_trades") val stopNewTrades: Boolean? = null,
    @SerialName("pause_ai") val pauseAi: Boolean? = null,
    @SerialName("emergency_stop") val emergencyStop: Boolean? = null,
    @SerialName("checked_at") val checkedAt: String? = null,
) {
    val mode: String? get() = tradingMode
    val dbOk: Boolean get() = database ?: databaseOk ?: false
    val redisOkValue: Boolean get() = redis ?: redisOk ?: false
    val mainnetOn: Boolean get() = mainnetEnabled ?: mainnetUnlocked
    val controlsValue: ControlStateDto
        get() = controls ?: ControlStateDto(
            stopNewTrades = stopNewTrades ?: false,
            pauseAi = pauseAi ?: false,
            emergencyStop = emergencyStop ?: false,
        )
}

@Serializable
data class ControlRequest(
    val action: String,
    val enabled: Boolean = true,
    val reason: String? = null,
)

// ---------------------------------------------------------------------------
// Autopilot (全自动 AI 代理)
// ---------------------------------------------------------------------------

@Serializable
data class WatchPlanDto(
    val symbol: String = "",
    val side: String = "",
    @SerialName("entry_low") val entryLow: String = "0",
    @SerialName("entry_high") val entryHigh: String = "0",
    @SerialName("stop_loss") val stopLoss: String? = null,
    @SerialName("take_profit") val takeProfit: String? = null,
    val leverage: Int = 1,
    val confidence: String = "0",
    val reason: String = "",
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("expires_at") val expiresAt: String? = null,
)

@Serializable
data class AutopilotConfigDto(
    val enabled: Boolean = false,
    @SerialName("auto_execute") val autoExecute: Boolean = false,
    @SerialName("execution_mode") val executionMode: String = "PAPER",
    val symbols: List<String> = emptyList(),
    @SerialName("watch_plans") val watchPlans: List<WatchPlanDto> = emptyList(),
    val interval: String = "15m",
    val intraday: Boolean = true,
    @SerialName("max_holding_minutes") val maxHoldingMinutes: Int = 240,
    @SerialName("cycle_seconds") val cycleSeconds: Int = 600,
    @SerialName("max_trades_per_day") val maxTradesPerDay: Int = 3,
    @SerialName("min_confidence") val minConfidence: String = "0.65",
    @SerialName("strategy_id") val strategyId: String? = null,
    @SerialName("last_run_at") val lastRunAt: String? = null,
    @SerialName("last_error") val lastError: String? = null,
    @SerialName("trades_today") val tradesToday: Int = 0,
    @SerialName("trading_mode") val tradingMode: String? = null,
)

@Serializable
data class AutopilotUpdateRequest(
    val enabled: Boolean? = null,
    @SerialName("auto_execute") val autoExecute: Boolean? = null,
    @SerialName("confirm_auto_execute") val confirmAutoExecute: Boolean = false,
    @SerialName("execution_mode") val executionMode: String? = null,
    val symbols: List<String>? = null,
    val interval: String? = null,
    val intraday: Boolean? = null,
    @SerialName("max_holding_minutes") val maxHoldingMinutes: Int? = null,
    @SerialName("cycle_seconds") val cycleSeconds: Int? = null,
    @SerialName("max_trades_per_day") val maxTradesPerDay: Int? = null,
    @SerialName("min_confidence") val minConfidence: Double? = null,
    @SerialName("strategy_id") val strategyId: String? = null,
)

@Serializable
data class PaperPositionDto(
    val id: String? = null,
    val symbol: String = "",
    @SerialName("position_side") val positionSide: String = "",
    val quantity: String = "0",
    @SerialName("entry_price") val entryPrice: String = "0",
    @SerialName("stop_loss") val stopLoss: String? = null,
    @SerialName("take_profit") val takeProfit: String? = null,
    val leverage: Int = 1,
    @SerialName("opened_at") val openedAt: String? = null,
)

@Serializable
data class LivePositionDto(
    val id: String? = null,
    val mode: String = "PAPER",
    val symbol: String = "",
    @SerialName("position_side") val positionSide: String = "",
    val quantity: String = "0",
    @SerialName("entry_price") val entryPrice: String = "0",
    @SerialName("stop_loss") val stopLoss: String? = null,
    @SerialName("take_profit") val takeProfit: String? = null,
    val leverage: Int = 1,
    val status: String = "OPEN",
    @SerialName("protection_status") val protectionStatus: String = "PENDING",
    @SerialName("opened_at") val openedAt: String? = null,
)

@Serializable
data class AutoRunDto(
    val id: String? = null,
    val symbol: String = "",
    val verdict: String? = null,
    val confidence: String? = null,
    val summary: String? = null,
    @SerialName("started_at") val startedAt: String? = null,
)

@Serializable
data class AutopilotStatusDto(
    val config: AutopilotConfigDto = AutopilotConfigDto(),
    @SerialName("open_positions") val openPositions: List<PaperPositionDto> = emptyList(),
    @SerialName("live_positions") val livePositions: List<LivePositionDto> = emptyList(),
    @SerialName("today_realized_pnl_usd") val todayRealizedPnlUsd: String = "0",
    @SerialName("total_trades") val totalTrades: Int = 0,
    @SerialName("recent_runs") val recentRuns: List<AutoRunDto> = emptyList(),
)

// ---------------------------------------------------------------------------
// Learning (学习与进化)
// ---------------------------------------------------------------------------

@Serializable
data class LessonDto(
    val id: String,
    val lesson: String = "",
    val category: String = "general",
    val tags: List<String> = emptyList(),
    val score: String = "0",
    val source: String = "",
    @SerialName("outcomes_count") val outcomesCount: Int = 0,
    val active: Boolean = true,
    @SerialName("created_at") val createdAt: String? = null,
)

@Serializable
data class LessonListDto(
    val items: List<LessonDto> = emptyList(),
)

@Serializable
data class TradeOutcomeDto(
    val id: String? = null,
    val symbol: String = "",
    @SerialName("position_side") val positionSide: String = "",
    val quantity: String = "0",
    @SerialName("entry_price") val entryPrice: String = "0",
    @SerialName("exit_price") val exitPrice: String = "0",
    @SerialName("pnl_usd") val pnlUsd: String = "0",
    @SerialName("r_multiple") val rMultiple: String? = null,
    @SerialName("exit_reason") val exitReason: String = "",
    val regime: String? = null,
    val confidence: String? = null,
    @SerialName("opened_at") val openedAt: String? = null,
    @SerialName("closed_at") val closedAt: String? = null,
)

@Serializable
data class SymbolStatDto(
    val trades: Int = 0,
    @SerialName("win_rate") val winRate: Double? = null,
    @SerialName("pnl_usd") val pnlUsd: String = "0",
)

@Serializable
data class EquityPointDto(
    val t: String = "",
    val pnl: String = "0",
    val equity: String = "0",
)

@Serializable
data class LearningPerformanceDto(
    @SerialName("total_trades") val totalTrades: Int = 0,
    val wins: Int = 0,
    @SerialName("win_rate") val winRate: Double? = null,
    @SerialName("avg_r_multiple") val avgRMultiple: String? = null,
    @SerialName("total_pnl_usd") val totalPnlUsd: String = "0",
    @SerialName("today_pnl_usd") val todayPnlUsd: String = "0",
    @SerialName("by_symbol") val bySymbol: Map<String, SymbolStatDto> = emptyMap(),
    @SerialName("by_exit_reason") val byExitReason: Map<String, Int> = emptyMap(),
    @SerialName("equity_curve") val equityCurve: List<EquityPointDto> = emptyList(),
    @SerialName("recent_outcomes") val recentOutcomes: List<TradeOutcomeDto> = emptyList(),
)

// ---------------------------------------------------------------------------
// Factor research (因子研究)
// ---------------------------------------------------------------------------

@Serializable
data class FactorResearchRequest(
    val symbol: String,
    val interval: String = "15m",
    @SerialName("horizon_bars") val horizonBars: Int = 8,
)

@Serializable
data class FactorProposalDto(
    val agent: String = "",
    val domain: String = "",
    @SerialName("factor_name") val factorName: String = "",
    val hypothesis: String = "",
    @SerialName("lookback_bars") val lookbackBars: Int = 0,
    val direction: String = "POSITIVE",
)

@Serializable
data class EvaluationDto(
    @SerialName("factor_name") val factorName: String = "",
    val domain: String = "",
    val agent: String = "",
    @SerialName("ic_mean") val icMean: Double? = null,
    @SerialName("ic_ir") val icIr: Double? = null,
    @SerialName("hit_rate") val hitRate: Double? = null,
    val coverage: Double? = null,
    val accepted: Boolean = false,
    val notes: List<String> = emptyList(),
)

@Serializable
data class FactorResearchReport(
    @SerialName("run_id") val runId: String = "",
    val symbol: String = "",
    val interval: String = "",
    @SerialName("horizon_bars") val horizonBars: Int? = null,
    val degraded: Boolean = false,
    val reasons: List<String> = emptyList(),
    val proposals: List<FactorProposalDto> = emptyList(),
    val evaluations: List<EvaluationDto> = emptyList(),
    @SerialName("accepted_count") val acceptedCount: Int = 0,
    @SerialName("rejected_count") val rejectedCount: Int = 0,
)

@Serializable
data class FactorLibraryItemDto(
    val id: String = "",
    val symbol: String = "",
    val interval: String = "",
    @SerialName("factor_name") val factorName: String = "",
    val domain: String = "",
    val agent: String = "",
    val direction: String = "",
    val formula: String = "",
    @SerialName("lookback_bars") val lookbackBars: Int = 0,
    @SerialName("ic_mean") val icMean: Double? = null,
    @SerialName("ic_ir") val icIr: Double? = null,
    @SerialName("hit_rate") val hitRate: Double? = null,
    val status: String = "",
    val hypothesis: String = "",
    @SerialName("created_at") val createdAt: String? = null,
)


@Serializable
data class FactorLifecycleReportDto(
    val checked: Int = 0,
    val changed: Int = 0,
    val items: List<FactorLifecycleItemDto> = emptyList(),
)

@Serializable
data class FactorLifecycleItemDto(
    @SerialName("factor_name") val factorName: String = "",
    val status: String = "",
    val reason: String? = null,
)

@Serializable
data class FactorSignalDto(
    @SerialName("factor_name") val factorName: String = "",
    val domain: String = "",
    val formula: String = "",
    val direction: String = "",
    @SerialName("normalized_signal") val normalizedSignal: Double? = null,
    val weight: Double? = null,
    @SerialName("base_weight") val baseWeight: Double? = null,
    @SerialName("regime_multiplier") val regimeMultiplier: Double? = null,
    @SerialName("ic_mean") val icMean: Double? = null,
    @SerialName("ic_ir") val icIr: Double? = null,
)

@Serializable
data class FactorStrategySuggestionDto(
    val type: String = "",
    val recommendation: String = "",
)

@Serializable
data class FactorStrategyCandidateDto(
    val name: String = "",
    @SerialName("entry_confirmation") val entryConfirmation: String = "",
    @SerialName("evaluation_required") val evaluationRequired: List<String> = emptyList(),
)

@Serializable
data class FactorIntelligenceDto(
    val symbol: String = "",
    val interval: String = "",
    @SerialName("candles_used") val candlesUsed: Int = 0,
    @SerialName("last_candle_at") val lastCandleAt: String? = null,
    @SerialName("market_regime") val marketRegime: FactorMarketRegimeDto = FactorMarketRegimeDto(),
    @SerialName("factor_signals") val factorSignals: FactorSignalsDto = FactorSignalsDto(),
    @SerialName("strategy_evolution") val strategyEvolution: FactorStrategyEvolutionDto = FactorStrategyEvolutionDto(),
)

@Serializable
data class FactorMarketRegimeDto(
    val label: String = "UNKNOWN",
    @SerialName("trend_strength") val trendStrength: Double? = null,
    val volatility: Double? = null,
    @SerialName("volatility_expansion") val volatilityExpansion: Double? = null,
    val confidence: Double = 0.0,
)

@Serializable
data class FactorSignalsDto(
    val available: Boolean = false,
    @SerialName("factor_count") val factorCount: Int = 0,
    @SerialName("composite_signal") val compositeSignal: Double? = null,
    val bias: String = "NO_VALIDATED_FACTORS",
    val factors: List<FactorSignalDto> = emptyList(),
    val policy: String = "",
)

@Serializable
data class FactorStrategyEvolutionDto(
    val mode: String = "SHADOW_RESEARCH_ONLY",
    val regime: String = "UNKNOWN",
    val suggestions: List<FactorStrategySuggestionDto> = emptyList(),
    @SerialName("candidate_strategies") val candidateStrategies: List<FactorStrategyCandidateDto> = emptyList(),
    @SerialName("auto_parameter_changes") val autoParameterChanges: Boolean = false,
    @SerialName("auto_orders") val autoOrders: Boolean = false,
)

@Serializable
data class FactorLibraryResponse(
    val items: List<FactorLibraryItemDto> = emptyList(),
)
