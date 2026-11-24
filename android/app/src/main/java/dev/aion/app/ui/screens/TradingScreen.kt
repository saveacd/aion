package dev.aion.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.ArrowDropDown
import androidx.compose.material.icons.outlined.Remove
import androidx.compose.material.icons.outlined.SwapVert
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import dev.aion.app.AppContainer
import dev.aion.app.data.ApiResult
import dev.aion.app.data.IntentResultDto
import dev.aion.app.data.IntentsResponse
import dev.aion.app.data.Loadable
import dev.aion.app.data.RiskPolicyDto
import dev.aion.app.data.RiskPolicyResponse
import dev.aion.app.data.SymbolDto
import dev.aion.app.data.SymbolsResponse
import dev.aion.app.data.TickerEnvelope
import dev.aion.app.data.TradeIntentRequest
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.AionField
import dev.aion.app.ui.components.ConfirmDialog
import dev.aion.app.ui.components.EmptyState
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.InfoBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.LabelText
import dev.aion.app.ui.components.ModePill
import dev.aion.app.ui.components.Num
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.components.SelectableChip
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.fmtNum
import dev.aion.app.ui.fmtPct
import dev.aion.app.ui.fmtUsd
import dev.aion.app.ui.pnlColor
import dev.aion.app.ui.sideColor
import dev.aion.app.ui.statusColor
import dev.aion.app.ui.theme.AionColors
import dev.aion.app.ui.toDecimal
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import java.math.BigDecimal
import java.math.RoundingMode
import java.util.Locale
import java.util.UUID

data class TradeFormState(
    val symbol: String = "",
    val direction: String = "LONG",
    val orderType: String = "MARKET",
    val notional: String = "100",
    val leverage: Int = 3,
    val entryPrice: String = "",
    val stopLoss: String = "",
    val takeProfit: String = "",
    val confidence: Float = 0.65f,
    val reason: String = "",
)

sealed interface IntentOutcome {
    data class Accepted(val result: IntentResultDto) : IntentOutcome
    data class Rejected(val result: IntentResultDto) : IntentOutcome
    data class Failed(val failure: ApiResult.Failure) : IntentOutcome
}

class TradingViewModel(container: AppContainer) : BaseViewModel(container) {

    val symbols = MutableStateFlow(Loadable<SymbolsResponse>())
    val ticker = MutableStateFlow(Loadable<TickerEnvelope>())
    val intents = MutableStateFlow(Loadable<IntentsResponse>())
    val policy = MutableStateFlow(Loadable<RiskPolicyResponse>())

    val form = MutableStateFlow(TradeFormState())
    val submitting = MutableStateFlow(false)
    val formError = MutableStateFlow<String?>(null)
    val outcome = MutableStateFlow<IntentOutcome?>(null)

    init {
        loadSymbols()
        load(intents) { container.repository.intents(8) }
        load(policy) { container.repository.policy() }
    }

    fun refresh() {
        loadSymbols()
        refreshTicker()
        load(intents) { container.repository.intents(8) }
        load(policy) { container.repository.policy() }
    }

    fun loadSymbols() {
        load(symbols) { container.repository.symbols() }
    }

    fun setSymbol(symbol: String) {
        if (form.value.symbol == symbol) return
        form.value = form.value.copy(symbol = symbol)
        ticker.value = Loadable()
        refreshTicker()
    }

    fun refreshTicker() {
        val symbol = form.value.symbol
        if (symbol.isBlank()) return
        load(ticker) { container.repository.snapshot(symbol) }
        refreshLive()
    }

    /** Overlay the server's 1-second live ticker; failures keep the last price. */
    fun refreshLive() {
        val symbol = form.value.symbol
        if (symbol.isBlank()) return
        viewModelScope.launch {
            when (val result = container.repository.livePrices()) {
                is ApiResult.Success -> {
                    val item = result.data.items.firstOrNull { it.symbol == symbol } ?: return@launch
                    val current = ticker.value.data
                        ?: TickerEnvelope(hasData = true, symbol = symbol)
                    val updated = current.copy(
                        hasData = true,
                        symbol = symbol,
                        lastPrice = item.price,
                        changePct = item.changePct24h ?: current.changePct,
                        ticker = current.ticker?.copy(
                            lastPrice = item.price,
                            priceChangePct24h = item.changePct24h
                                ?: current.ticker.priceChangePct24h,
                            stale = false,
                        ),
                    )
                    ticker.value = ticker.value.copy(data = updated, loading = false, error = null)
                }
                is ApiResult.Failure -> Unit
            }
        }
    }

    fun setDirection(direction: String) {
        form.value = form.value.copy(direction = direction)
    }

    fun setOrderType(type: String) {
        form.value = form.value.copy(orderType = type)
    }

    fun setNotional(value: String) {
        if (value.length <= 14) form.value = form.value.copy(notional = sanitizeDecimal(value))
    }

    fun setEntryPrice(value: String) {
        form.value = form.value.copy(entryPrice = sanitizeDecimal(value))
    }

    fun setStopLoss(value: String) {
        form.value = form.value.copy(stopLoss = sanitizeDecimal(value))
    }

    fun setTakeProfit(value: String) {
        form.value = form.value.copy(takeProfit = sanitizeDecimal(value))
    }

    fun setLeverage(value: Int, max: Int) {
        form.value = form.value.copy(leverage = value.coerceIn(1, max.coerceAtLeast(1)))
    }

    fun setConfidence(value: Float) {
        form.value = form.value.copy(confidence = value.coerceIn(0.05f, 1f))
    }

    fun setReason(value: String) {
        form.value = form.value.copy(reason = value.take(500))
    }

    fun clearOutcome() {
        outcome.value = null
        formError.value = null
    }

    fun validate(): String? {
        val f = form.value
        if (f.symbol.isBlank()) return "请选择交易对"
        val notional = toDecimal(f.notional)
        if (notional == null || notional.signum() <= 0) return "名义价值需大于 0"
        if (f.leverage < 1) return "杠杆至少为 1x"
        val entry = referencePrice(f, ticker.value.data?.priceValue)
        if (f.orderType == "LIMIT" && (entry == null || entry.signum() <= 0)) {
            return "限价单需要填写有效的委托价格"
        }
        val stop = toDecimal(f.stopLoss)
        val take = toDecimal(f.takeProfit)
        if (stop != null && stop.signum() <= 0) return "止损价格需大于 0"
        if (take != null && take.signum() <= 0) return "止盈价格需大于 0"
        if (entry != null && entry.signum() > 0) {
            if (f.direction == "LONG") {
                if (stop != null && stop >= entry) return "做多止损价必须低于入场价"
                if (take != null && take <= entry) return "做多止盈价必须高于入场价"
            } else {
                if (stop != null && stop <= entry) return "做空止损价必须高于入场价"
                if (take != null && take >= entry) return "做空止盈价必须低于入场价"
            }
        }
        return null
    }

    fun submit() {
        if (submitting.value) return
        val error = validate()
        if (error != null) {
            formError.value = error
            return
        }
        val f = form.value
        formError.value = null
        submitting.value = true
        outcome.value = null

        val confidence = String.format(Locale.US, "%.2f", f.confidence)
        val reason = buildString {
            append("confidence=").append(confidence)
            if (f.reason.isNotBlank()) append(" | ").append(f.reason.trim())
        }.take(2000)

        val request = TradeIntentRequest(
            symbol = f.symbol,
            side = if (f.direction == "LONG") "BUY" else "SELL",
            positionSide = f.direction,
            orderType = f.orderType,
            notionalUsd = toDecimal(f.notional)?.toPlainString(),
            requestedLeverage = f.leverage,
            entryPrice = if (f.orderType == "LIMIT") toDecimal(f.entryPrice)?.toPlainString() else null,
            stopLoss = toDecimal(f.stopLoss)?.toPlainString(),
            takeProfit = toDecimal(f.takeProfit)?.toPlainString(),
            timeInForce = "GTC",
            reason = reason,
            clientIntentKey = UUID.randomUUID().toString(),
        )

        viewModelScope.launch {
            val result = container.repository.submitIntent(request)
            outcome.value = when (result) {
                is ApiResult.Success -> {
                    val approved = result.data.risk?.approved == true || result.data.accepted
                    if (approved) IntentOutcome.Accepted(result.data) else IntentOutcome.Rejected(result.data)
                }
                is ApiResult.Failure -> IntentOutcome.Failed(result)
            }
            submitting.value = false
            if (result is ApiResult.Success) {
                load(intents) { container.repository.intents(8) }
            }
        }
    }

    private fun sanitizeDecimal(value: String): String {
        val filtered = value.filter { it.isDigit() || it == '.' }
        val firstDot = filtered.indexOf('.')
        if (firstDot < 0) return filtered
        return filtered.substring(0, firstDot + 1) +
            filtered.substring(firstDot + 1).replace(".", "")
    }
}

private fun referencePrice(form: TradeFormState, tickerPrice: String?): BigDecimal? =
    if (form.orderType == "LIMIT") {
        toDecimal(form.entryPrice)
    } else {
        toDecimal(tickerPrice) ?: toDecimal(form.entryPrice)
    }

@Composable
fun TradingScreen(container: AppContainer) {
    val vm: TradingViewModel = aionViewModel(container) { TradingViewModel(it) }
    val symbols by vm.symbols.collectAsState()
    val ticker by vm.ticker.collectAsState()
    val intents by vm.intents.collectAsState()
    val policy by vm.policy.collectAsState()
    val form by vm.form.collectAsState()
    val submitting by vm.submitting.collectAsState()
    val formError by vm.formError.collectAsState()
    val outcome by vm.outcome.collectAsState()
    val system by container.appState.system.collectAsState()

    val symbolList = symbols.data?.items.orEmpty()
    val selectedSymbol = symbolList.firstOrNull { it.symbol == form.symbol }
    val maxLeverage = selectedSymbol?.maxLeverage?.takeIf { it > 0 } ?: 20
    val policyDto = policy.data?.policy
    val mode = system?.mode
    var showConfirm by remember { mutableStateOf(false) }

    GlobalRefreshEffect(container) { vm.refresh() }

    LaunchedEffect(symbols.data) {
        if (vm.form.value.symbol.isBlank()) {
            symbolList.firstOrNull()?.symbol?.let { vm.setSymbol(it) }
        }
    }

    LaunchedEffect(form.symbol) {
        while (true) {
            vm.refreshLive()
            delay(1_000)
        }
    }

    val summary = remember(form, ticker.data, policyDto) {
        buildSummary(form, ticker.data?.priceValue, policyDto)
    }

    if (showConfirm) {
        ConfirmDialog(
            title = "确认提交交易意图",
            message = summaryBlockText(form, summary),
            confirmLabel = "确认提交",
            onDismiss = { showConfirm = false },
            onConfirm = {
                showConfirm = false
                vm.submit()
            },
        )
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (mode == null || !mode.equals("MAINNET", ignoreCase = true)) {
            item {
                InfoBanner(
                    text = if (mode == null) {
                        "交易模式未知，请稍后刷新后再下单。"
                    } else {
                        "当前交易模式为 $mode：指令不会进入真实主网账户。PAPER 模式仅记录模拟结果。"
                    },
                    color = if (mode == null) AionColors.Red else AionColors.Amber,
                )
            }
        }

        item {
            AionCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("新建交易意图", style = MaterialTheme.typography.titleMedium)
                        Text(
                            "先经服务端风控，再交由执行引擎",
                            style = MaterialTheme.typography.labelSmall,
                            color = AionColors.Muted,
                        )
                    }
                    ModePill(mode)
                }
                Spacer(Modifier.height(14.dp))

                when {
                    symbols.data == null && symbols.error != null -> ErrorBanner(symbols.error ?: "", { vm.loadSymbols() })
                    symbols.data == null -> SkeletonBlock(48.dp)
                    symbolList.isEmpty() -> EmptyState(
                        title = "暂无可交易对",
                        message = "交易对列表正在同步，暂时无法创建交易意图。",
                        icon = Icons.Outlined.SwapVert,
                    )
                    else -> {
                        SymbolPicker(
                            symbols = symbolList,
                            selected = form.symbol,
                            onSelect = { vm.setSymbol(it) },
                        )
                        Spacer(Modifier.height(14.dp))

                        LabelText("方向")
                        Spacer(Modifier.height(8.dp))
                        DirectionSelector(form.direction) { vm.setDirection(it) }

                        Spacer(Modifier.height(14.dp))
                        LabelText("订单类型")
                        Spacer(Modifier.height(8.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            SelectableChip("市价 MARKET", form.orderType == "MARKET", { vm.setOrderType("MARKET") })
                            SelectableChip("限价 LIMIT", form.orderType == "LIMIT", { vm.setOrderType("LIMIT") })
                        }

                        Spacer(Modifier.height(14.dp))
                        AionField(
                            value = form.notional,
                            onValueChange = { vm.setNotional(it) },
                            label = "名义价值",
                            mono = true,
                            keyboardType = KeyboardType.Decimal,
                            placeholder = "100",
                            trailing = {
                                Text(
                                    "USD",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AionColors.Muted,
                                )
                            },
                            supporting = selectedSymbol?.minNotional?.let {
                                "最小名义价值 ${fmtNum(it, 2)} USD"
                            },
                        )

                        if (form.orderType == "LIMIT") {
                            Spacer(Modifier.height(12.dp))
                            AionField(
                                value = form.entryPrice,
                                onValueChange = { vm.setEntryPrice(it) },
                                label = "委托价格",
                                mono = true,
                                keyboardType = KeyboardType.Decimal,
                                placeholder = ticker.data?.priceValue?.let { fmtNum(it, 6) } ?: "0.00",
                            )
                        }

                        Spacer(Modifier.height(14.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(modifier = Modifier.weight(1f)) {
                                LabelText("杠杆")
                                Spacer(Modifier.height(4.dp))
                                Text(
                                    "该交易对最大 ${selectedSymbol?.maxLeverage ?: "—"}x",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AionColors.Muted,
                                )
                            }
                            IconButton(onClick = { vm.setLeverage(form.leverage - 1, maxLeverage) }) {
                                Icon(Icons.Outlined.Remove, "减少杠杆", tint = AionColors.Muted)
                            }
                            Box(
                                modifier = Modifier
                                    .width(62.dp)
                                    .clip(RoundedCornerShape(10.dp))
                                    .background(AionColors.CardHigh)
                                    .padding(vertical = 8.dp),
                                contentAlignment = Alignment.Center,
                            ) {
                                Num(
                                    "${form.leverage}x",
                                    style = MaterialTheme.typography.titleMedium,
                                    color = AionColors.Primary,
                                    weight = FontWeight.SemiBold,
                                )
                            }
                            IconButton(onClick = { vm.setLeverage(form.leverage + 1, maxLeverage) }) {
                                Icon(Icons.Outlined.Add, "增加杠杆", tint = AionColors.Muted)
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            listOf(1, 3, 5, 10).filter { it <= maxLeverage }.forEach { value ->
                                SelectableChip(
                                    label = "${value}x",
                                    selected = form.leverage == value,
                                    onClick = { vm.setLeverage(value, maxLeverage) },
                                )
                            }
                        }

                        Spacer(Modifier.height(14.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            AionField(
                                value = form.stopLoss,
                                onValueChange = { vm.setStopLoss(it) },
                                label = "止损价格",
                                mono = true,
                                keyboardType = KeyboardType.Decimal,
                                modifier = Modifier.weight(1f),
                            )
                            AionField(
                                value = form.takeProfit,
                                onValueChange = { vm.setTakeProfit(it) },
                                label = "止盈价格",
                                mono = true,
                                keyboardType = KeyboardType.Decimal,
                                modifier = Modifier.weight(1f),
                            )
                        }
                        if (policyDto?.requiredStopLoss == true && form.stopLoss.isBlank()) {
                            Spacer(Modifier.height(6.dp))
                            Text(
                                "服务端风控要求必须设置止损，缺失会被拒绝。",
                                style = MaterialTheme.typography.labelSmall,
                                color = AionColors.Amber,
                            )
                        }

                        Spacer(Modifier.height(16.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            LabelText("置信度")
                            Spacer(Modifier.weight(1f))
                            Num(
                                String.format(Locale.US, "%.2f", form.confidence),
                                style = MaterialTheme.typography.bodyMedium,
                                color = AionColors.Primary,
                            )
                        }
                        Slider(
                            value = form.confidence,
                            onValueChange = { vm.setConfidence(it) },
                            valueRange = 0.05f..1f,
                            colors = SliderDefaults.colors(
                                thumbColor = AionColors.Primary,
                                activeTrackColor = AionColors.Primary,
                                inactiveTrackColor = AionColors.CardHigh,
                            ),
                        )

                        Spacer(Modifier.height(8.dp))
                        AionField(
                            value = form.reason,
                            onValueChange = { vm.setReason(it) },
                            label = "交易理由",
                            placeholder = "简述触发条件、结构与风险假设",
                            singleLine = false,
                            supporting = "置信度会写入 reason（confidence=…），服务端意图接口没有独立字段。",
                        )

                        Spacer(Modifier.height(16.dp))
                        RiskSummaryBlock(form, summary)

                        Spacer(Modifier.height(16.dp))
                        if (formError != null) {
                            InfoBanner(formError ?: "", AionColors.Red)
                            Spacer(Modifier.height(10.dp))
                        }
                        Button(
                            onClick = { showConfirm = true },
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(50.dp),
                            enabled = !submitting,
                            colors = ButtonDefaults.buttonColors(
                                containerColor = if (form.direction == "LONG") AionColors.Green else AionColors.Red,
                                disabledContainerColor = AionColors.CardHigh,
                            ),
                            shape = RoundedCornerShape(12.dp),
                        ) {
                            if (submitting) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(18.dp),
                                    strokeWidth = 2.dp,
                                    color = AionColors.Text,
                                )
                                Spacer(Modifier.width(10.dp))
                                Text("提交中…")
                            } else {
                                Text(
                                    if (form.direction == "LONG") "提交做多意图" else "提交做空意图",
                                    style = MaterialTheme.typography.titleMedium,
                                )
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "提交后按钮会锁定，避免重复下单。",
                            style = MaterialTheme.typography.labelSmall,
                            color = AionColors.Muted,
                        )
                    }
                }
            }
        }

        outcome?.let { result ->
            item {
                IntentResultCard(result) { vm.clearOutcome() }
            }
        }

        item {
            AionCard {
                SectionHeader(
                    title = "最近交易意图",
                    subtitle = "来自 /trading/intents?limit=8",
                )
                Spacer(Modifier.height(10.dp))
                when {
                    intents.data == null && intents.error != null -> ErrorBanner(
                        intents.error ?: "",
                        { vm.refresh() },
                    )
                    intents.data == null -> SkeletonBlock(56.dp)
                    intents.data?.items.isNullOrEmpty() -> EmptyState(
                        title = "暂无交易意图",
                        message = "还没有通过 AI 或手动方式创建的意图记录。",
                        icon = Icons.Outlined.SwapVert,
                    )
                    else -> intents.data?.items.orEmpty().forEachIndexed { index, intent ->
                        if (index > 0) {
                            HorizontalDivider(
                                color = AionColors.Border.copy(alpha = 0.6f),
                                modifier = Modifier.padding(vertical = 2.dp),
                            )
                        }
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(
                                        intent.symbol,
                                        style = MaterialTheme.typography.bodyMedium.copy(
                                            fontFamily = FontFamily.Monospace,
                                        ),
                                    )
                                    Spacer(Modifier.width(6.dp))
                                    Pill(intent.positionSide, sideColor(intent.positionSide))
                                    if (intent.rejectionCode != null) {
                                        Spacer(Modifier.width(6.dp))
                                        Pill(intent.rejectionCode, AionColors.Red)
                                    }
                                }
                                Text(
                                    "${intent.orderType ?: "—"} · ${fmtUsd(intent.notionalUsd)} · ${intent.requestedLeverage}x · ${formatTime(intent.createdAt)}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AionColors.Muted,
                                )
                            }
                            Pill(intent.status, statusColor(intent.status))
                        }
                    }
                }
            }
        }

        item { Spacer(Modifier.height(6.dp)) }
    }
}

@Composable
private fun SymbolPicker(symbols: List<SymbolDto>, selected: String, onSelect: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(
            onClick = { expanded = true },
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier.fillMaxWidth(),
            contentPadding = PaddingValues(horizontal = 14.dp, vertical = 14.dp),
        ) {
            Column(modifier = Modifier.weight(1f), horizontalAlignment = Alignment.Start) {
                Text(
                    "交易对",
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                )
                Text(
                    selected.ifBlank { "选择交易对" },
                    style = MaterialTheme.typography.titleMedium.copy(fontFamily = FontFamily.Monospace),
                    color = AionColors.Text,
                )
            }
            Icon(Icons.Outlined.ArrowDropDown, contentDescription = null, tint = AionColors.Muted)
        }
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
            modifier = Modifier
                .heightIn(max = 340.dp)
                .background(AionColors.CardHigh),
        ) {
            symbols.forEach { symbol ->
                DropdownMenuItem(
                    text = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                symbol.symbol,
                                style = MaterialTheme.typography.bodyMedium.copy(
                                    fontFamily = FontFamily.Monospace,
                                ),
                                modifier = Modifier.weight(1f),
                            )
                            Text(
                                "${symbol.maxLeverage ?: "—"}x",
                                style = MaterialTheme.typography.labelSmall,
                                color = AionColors.Muted,
                            )
                        }
                    },
                    onClick = {
                        expanded = false
                        onSelect(symbol.symbol)
                    },
                )
            }
        }
    }
}

@Composable
private fun DirectionSelector(direction: String, onChange: (String) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        listOf("LONG" to "做多", "SHORT" to "做空").forEach { (value, label) ->
            val active = direction == value
            val color = if (value == "LONG") AionColors.Green else AionColors.Red
            val shape = RoundedCornerShape(12.dp)
            Box(
                modifier = Modifier
                    .weight(1f)
                    .clip(shape)
                    .background(if (active) color.copy(alpha = 0.16f) else AionColors.CardHigh)
                    .border(1.dp, if (active) color.copy(alpha = 0.6f) else AionColors.Border, shape)
                    .clickable { onChange(value) }
                    .padding(vertical = 11.dp),
                contentAlignment = Alignment.Center,
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        label,
                        style = MaterialTheme.typography.titleMedium,
                        color = if (active) color else AionColors.Muted,
                    )
                    Text(
                        if (value == "LONG") "BUY · LONG" else "SELL · SHORT",
                        style = MaterialTheme.typography.labelSmall,
                        color = AionColors.Muted,
                    )
                }
            }
        }
    }
}

private data class RiskSummary(
    val reference: BigDecimal?,
    val margin: BigDecimal?,
    val stopLossUsd: BigDecimal?,
    val rewardRisk: BigDecimal?,
    val notional: BigDecimal?,
)

private fun buildSummary(
    form: TradeFormState,
    tickerPrice: String?,
    policy: RiskPolicyDto?,
): RiskSummary {
    val notional = toDecimal(form.notional)
    val leverage = form.leverage.coerceAtLeast(1)
    val margin = notional?.divide(BigDecimal(leverage), 2, RoundingMode.HALF_UP)
    val reference = referencePrice(form, tickerPrice)
    val stop = toDecimal(form.stopLoss)
    val take = toDecimal(form.takeProfit)
    val stopLossUsd = if (reference != null && stop != null && reference.signum() > 0 && notional != null) {
        notional.multiply(reference.subtract(stop).abs())
            .divide(reference, 2, RoundingMode.HALF_UP)
    } else {
        null
    }
    val risk = if (reference != null && stop != null) reference.subtract(stop).abs() else null
    val reward = if (reference != null && take != null) take.subtract(reference).abs() else null
    val rr = if (risk != null && reward != null && risk.signum() > 0) {
        reward.divide(risk, 2, RoundingMode.HALF_UP)
    } else {
        null
    }
    return RiskSummary(
        reference = reference,
        margin = margin,
        stopLossUsd = stopLossUsd,
        rewardRisk = rr,
        notional = notional,
    )
}

@Composable
private fun RiskSummaryBlock(form: TradeFormState, summary: RiskSummary) {
    val shape = RoundedCornerShape(12.dp)
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(AionColors.CardHigh)
            .padding(14.dp),
    ) {
        Text("风险摘要（下单前自查）", style = MaterialTheme.typography.titleSmall)
        Spacer(Modifier.height(8.dp))
        KeyValueRow("参考价格", fmtNum(summary.reference?.toPlainString()), mono = true)
        KeyValueRow("名义价值", fmtUsd(summary.notional?.toPlainString()))
        KeyValueRow(
            "预计保证金",
            fmtUsd(summary.margin?.toPlainString()),
        )
        KeyValueRow(
            "止损最大亏损（估算）",
            if (summary.stopLossUsd != null) fmtUsd(summary.stopLossUsd.toPlainString()) else "—（未设止损或缺少参考价）",
            valueColor = if (summary.stopLossUsd != null) AionColors.Amber else AionColors.Muted,
        )
        KeyValueRow(
            "盈亏比 R:R",
            summary.rewardRisk?.let { "1 : ${it.toPlainString()}" } ?: "—",
        )
        KeyValueRow("方向", form.direction, mono = false, valueColor = sideColor(form.direction))
        KeyValueRow("杠杆", "${form.leverage}x")
        Spacer(Modifier.height(4.dp))
        Text(
            "以上为客户端近似估算，最终以服务端 /risk 判定为准。",
            style = MaterialTheme.typography.labelSmall,
            color = AionColors.Muted,
        )
    }
}

private fun summaryBlockText(form: TradeFormState, summary: RiskSummary): String = buildString {
    append("${form.symbol} · ${if (form.direction == "LONG") "做多" else "做空"} · ${form.orderType}\n")
    append("名义价值：${fmtUsd(summary.notional?.toPlainString())}\n")
    append("杠杆：${form.leverage}x，预计保证金 ${fmtUsd(summary.margin?.toPlainString())}\n")
    append("参考价：${fmtNum(summary.reference?.toPlainString())}\n")
    append("止损：${form.stopLoss.ifBlank { "未设置" }}，止盈：${form.takeProfit.ifBlank { "未设置" }}\n")
    append("提交后将进入服务端风控，可能被拒绝（422 或 accepted=false）。")
}

@Composable
private fun IntentResultCard(outcome: IntentOutcome, onClear: () -> Unit) {
    when (outcome) {
        is IntentOutcome.Accepted -> {
            val result = outcome.result
            AionCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("风控通过", style = MaterialTheme.typography.titleMedium, color = AionColors.Green)
                    Spacer(Modifier.width(8.dp))
                    Pill(result.effectiveStatus ?: "ACCEPTED", AionColors.Green, filled = true)
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = onClear) { Text("清除", color = AionColors.Muted) }
                }
                Spacer(Modifier.height(10.dp))
                KeyValueRow("意图 ID", result.effectiveId ?: "—")
                KeyValueRow("状态", result.effectiveStatus ?: "—")
                KeyValueRow("风控批准", "是", valueColor = AionColors.Green, mono = false)
                if (!result.risk?.reasonCodes.isNullOrEmpty()) {
                    KeyValueRow(
                        "原因码",
                        result.risk?.reasonCodes?.joinToString(", ") ?: "—",
                        valueColor = AionColors.Amber,
                    )
                }
                if (result.orders.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    KeyValueRow("生成订单", "${result.orders.size} 笔")
                    result.orders.take(3).forEach { order ->
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                "${order.symbol} · ${order.side} · ${order.orderType}",
                                style = MaterialTheme.typography.bodySmall,
                                color = AionColors.Muted,
                                modifier = Modifier.weight(1f),
                            )
                            Pill(order.status, statusColor(order.status))
                        }
                    }
                }
                if (result.orders.any { it.errorMessage != null }) {
                    Spacer(Modifier.height(6.dp))
                    InfoBanner(
                        result.orders.firstNotNullOfOrNull { it.errorMessage } ?: "",
                        AionColors.Amber,
                    )
                }
            }
        }

        is IntentOutcome.Rejected -> {
            val result = outcome.result
            AionCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("风控拒绝", style = MaterialTheme.typography.titleMedium, color = AionColors.Red)
                    Spacer(Modifier.width(8.dp))
                    Pill(result.effectiveStatus ?: "REJECTED", AionColors.Red, filled = true)
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = onClear) { Text("清除", color = AionColors.Muted) }
                }
                Spacer(Modifier.height(10.dp))
                KeyValueRow("意图 ID", result.effectiveId ?: "—")
                val codes = result.risk?.reasonCodes.orEmpty()
                if (codes.isEmpty()) {
                    Text(
                        "服务端未返回 reason_codes。",
                        style = MaterialTheme.typography.bodySmall,
                        color = AionColors.Muted,
                    )
                } else {
                    LabelText("reason_codes（原样）")
                    Spacer(Modifier.height(6.dp))
                    codes.forEach { code ->
                        Text(
                            "• $code",
                            style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                            color = AionColors.Red,
                        )
                    }
                }
                result.intent?.rejectionDetail?.let { detail ->
                    Spacer(Modifier.height(8.dp))
                    LabelText("拒绝详情")
                    Text(
                        detail.toString(),
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                        color = AionColors.Muted,
                    )
                }
            }
        }

        is IntentOutcome.Failed -> {
            val failure = outcome.failure
            AionCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("提交失败", style = MaterialTheme.typography.titleMedium, color = AionColors.Red)
                        Text(
                            failure.httpStatus?.let { "HTTP $it" } ?: (failure.code ?: "CLIENT"),
                            style = MaterialTheme.typography.labelSmall,
                            color = AionColors.Muted,
                        )
                    }
                    TextButton(onClick = onClear) { Text("清除", color = AionColors.Muted) }
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    failure.message,
                    style = MaterialTheme.typography.bodyMedium,
                    color = AionColors.Text,
                )
                failure.details?.let { details ->
                    Spacer(Modifier.height(10.dp))
                    LabelText("服务端详情（原样）")
                    Spacer(Modifier.height(4.dp))
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(max = 220.dp)
                            .clip(RoundedCornerShape(10.dp))
                            .background(AionColors.Bg)
                            .padding(10.dp)
                            .verticalScroll(rememberScrollState()),
                    ) {
                        Text(
                            details,
                            style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                            color = AionColors.Muted,
                        )
                    }
                }
                if (failure.requestId != null) {
                    Spacer(Modifier.height(8.dp))
                    Num(
                        "request_id: ${failure.requestId}",
                        style = MaterialTheme.typography.labelSmall,
                        color = AionColors.Muted,
                    )
                }
            }
        }
    }
}
