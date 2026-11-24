package dev.aion.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ShowChart
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
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
import dev.aion.app.data.CandleSeries
import dev.aion.app.data.Loadable
import dev.aion.app.data.SymbolDto
import dev.aion.app.data.SymbolsResponse
import dev.aion.app.data.TickerEnvelope
import dev.aion.app.data.WsEvent
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.AionField
import dev.aion.app.ui.components.EmptyState
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.ModePill
import dev.aion.app.ui.components.Num
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.PillTabs
import dev.aion.app.ui.components.PriceLineChart
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.components.SelectableChip
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.fmtNum
import dev.aion.app.ui.fmtPct
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.pnlColor
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.math.BigDecimal
import java.math.RoundingMode

private val intervals = listOf("15m", "1h", "4h", "1d")

class MarketViewModel(container: AppContainer) : BaseViewModel(container) {

    val symbols = MutableStateFlow(Loadable<SymbolsResponse>())
    val ticker = MutableStateFlow(Loadable<TickerEnvelope>())
    val candles = MutableStateFlow(Loadable<CandleSeries>())
    val liveAgeMs = MutableStateFlow<Long?>(null)

    val selected = MutableStateFlow<String?>(null)
    val interval = MutableStateFlow("15m")
    val search = MutableStateFlow("")

    init {
        loadSymbols()
        viewModelScope.launch {
            container.realtime.events.collect { event -> onRealtime(event) }
        }
    }

    fun refresh() {
        loadSymbols()
        refreshTicker()
        refreshCandles()
    }

    fun loadSymbols() {
        load(symbols) { container.repository.symbols() }
    }

    fun select(symbol: String) {
        if (selected.value == symbol) return
        selected.value = symbol
        ticker.value = Loadable()
        candles.value = Loadable()
        liveAgeMs.value = null
        refreshTicker()
        refreshCandles()
    }

    fun setInterval(value: String) {
        if (interval.value == value) return
        interval.value = value
        refreshCandles()
    }

    fun refreshTicker() {
        val symbol = selected.value ?: return
        load(ticker) { container.repository.snapshot(symbol) }
        // Overlay the live ticker immediately; the snapshot only fills
        // auxiliary fields (mark price, 24h volume).
        refreshLive()
    }

    /** Pull the server's 1-second ticker feed; never shows a loading state. */
    fun refreshLive() {
        val symbol = selected.value ?: return
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
                        capturedAt = item.capturedAt ?: current.capturedAt,
                        ticker = current.ticker?.copy(
                            lastPrice = item.price,
                            priceChangePct24h = item.changePct24h
                                ?: current.ticker.priceChangePct24h,
                            sourceTs = item.capturedAt ?: current.ticker.sourceTs,
                            stale = false,
                        ),
                    )
                    liveAgeMs.value = item.ageMs
                    ticker.value = ticker.value.copy(
                        data = updated, loading = false, error = null,
                    )
                }
                is ApiResult.Failure -> Unit // keep the last price on transient errors
            }
        }
    }

    fun refreshCandles() {
        val symbol = selected.value ?: return
        load(candles) { container.repository.candles(symbol, interval.value, 160) }
    }

    fun updateSearch(value: String) {
        search.value = value
    }

    fun visibleSymbols(all: List<SymbolDto>): List<SymbolDto> {
        val query = search.value.trim().uppercase()
        if (query.isEmpty()) return all
        return all.filter { it.symbol.uppercase().contains(query) }
    }

    private fun onRealtime(event: WsEvent) {
        val type = event.type.lowercase()
        if (!type.contains("ticker") && !type.contains("market") && !type.contains("price")) return
        val payload = event.payload as? JsonObject ?: return
        val symbol = (payload["symbol"] as? JsonPrimitive)?.content ?: return
        if (symbol != selected.value) return
        val price = (payload["last_price"] as? JsonPrimitive)?.content
            ?: (payload["price"] as? JsonPrimitive)?.content
            ?: return
        val existing = ticker.value.data
        val updated = if (existing == null) {
            TickerEnvelope(hasData = true, symbol = symbol, lastPrice = price)
        } else {
            existing.copy(
                lastPrice = price,
                ticker = existing.ticker?.copy(lastPrice = price),
            )
        }
        ticker.value = ticker.value.copy(data = updated, error = null)
    }
}

@Composable
fun MarketScreen(container: AppContainer) {
    val vm: MarketViewModel = aionViewModel(container) { MarketViewModel(it) }
    val symbols by vm.symbols.collectAsState()
    val ticker by vm.ticker.collectAsState()
    val liveAge by vm.liveAgeMs.collectAsState()
    val candles by vm.candles.collectAsState()
    val selected by vm.selected.collectAsState()
    val interval by vm.interval.collectAsState()
    val search by vm.search.collectAsState()
    val system by container.appState.system.collectAsState()

    val symbolList = symbols.data?.items.orEmpty()
    val visible = remember(symbolList, search) { vm.visibleSymbols(symbolList) }

    GlobalRefreshEffect(container) { vm.refresh() }

    LaunchedEffect(symbols.data) {
        if (vm.selected.value == null) {
            symbolList.firstOrNull()?.symbol?.let { vm.select(it) }
        }
    }

    LaunchedEffect(Unit) {
        while (true) {
            delay(1_000)
            vm.refreshLive()
        }
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PriceHeader(
                symbol = selected,
                ticker = ticker,
                mode = system?.mode,
                liveAgeMs = liveAge,
                onRetry = { vm.refreshTicker() },
            )
        }

        item {
            AionCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("价格图表", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.width(8.dp))
                    Pill(interval, AionColors.Primary)
                    Spacer(Modifier.weight(1f))
                    if (candles.data?.stale == true) {
                        Pill("数据过期", AionColors.Amber)
                    }
                }
                Spacer(Modifier.height(12.dp))
                PillTabs(
                    labels = intervals,
                    selected = intervals.indexOf(interval).coerceAtLeast(0),
                    onSelect = { index -> vm.setInterval(intervals[index]) },
                )
                Spacer(Modifier.height(14.dp))
                CandleChart(
                    series = candles,
                    onRetry = { vm.refreshCandles() },
                )
            }
        }

        item {
            Column(modifier = Modifier.padding(top = 4.dp)) {
                SectionHeader(
                    title = "交易对",
                    subtitle = if (symbols.data != null) {
                        "共 ${symbolList.size} 个可交易合约 · 价格仅展示当前选中交易对"
                    } else {
                        "来自 /market/symbols"
                    },
                )
                Spacer(Modifier.height(10.dp))
                AionField(
                    value = search,
                    onValueChange = { vm.updateSearch(it) },
                    label = "搜索交易对",
                    placeholder = "例如 BTC",
                    keyboardType = KeyboardType.Text,
                )
            }
        }

        when {
            symbols.data == null && symbols.error != null -> item {
                Column {
                    ErrorBanner(symbols.error ?: "", { vm.loadSymbols() })
                }
            }
            symbols.data == null -> item {
                SkeletonBlock(64.dp)
                Spacer(Modifier.height(8.dp))
                SkeletonBlock(64.dp)
                Spacer(Modifier.height(8.dp))
                SkeletonBlock(64.dp)
            }
            symbolList.isEmpty() -> item {
                AionCard(padding = PaddingValues(0.dp)) {
                    EmptyState(
                        title = "暂无交易对",
                        message = "交易对列表正在同步，请稍后刷新。",
                        icon = Icons.Outlined.ShowChart,
                    )
                }
            }
            visible.isEmpty() -> item {
                AionCard(padding = PaddingValues(0.dp)) {
                    EmptyState(
                        title = "没有匹配的交易对",
                        message = "尝试清空搜索关键字。",
                        icon = Icons.Outlined.ShowChart,
                    )
                }
            }
            else -> items(visible, key = { it.symbol }) { symbol ->
                SymbolRow(
                    symbol = symbol,
                    selected = symbol.symbol == selected,
                    onClick = { vm.select(symbol.symbol) },
                )
            }
        }

        item { Spacer(Modifier.height(4.dp)) }
    }
}

@Composable
private fun PriceHeader(
    symbol: String?,
    ticker: Loadable<TickerEnvelope>,
    mode: String?,
    liveAgeMs: Long?,
    onRetry: () -> Unit,
) {
    AionCard(padding = PaddingValues(18.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                symbol ?: "—",
                style = MaterialTheme.typography.headlineSmall.copy(
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.SemiBold,
                ),
            )
            Spacer(Modifier.width(8.dp))
            ModePill(mode)
            Spacer(Modifier.weight(1f))
            when {
                liveAgeMs != null && liveAgeMs <= 5_000 ->
                    Pill("实时", AionColors.Green, filled = true)
                liveAgeMs != null -> Pill("延迟", AionColors.Amber, filled = true)
                ticker.data?.isStale == true -> Pill("行情过期", AionColors.Amber, filled = true)
            }
        }
        Spacer(Modifier.height(12.dp))

        val data = ticker.data
        when {
            data == null && ticker.error != null -> ErrorBanner(ticker.error, onRetry)
            data == null -> {
                SkeletonBlock(46.dp)
                Spacer(Modifier.height(8.dp))
                SkeletonBlock(20.dp)
            }
            data.priceValue == null -> EmptyState(
                title = "暂无行情",
                message = "该交易对的行情正在同步，请稍后刷新。",
                icon = Icons.Outlined.ShowChart,
            )
            else -> {
                Row(verticalAlignment = Alignment.Bottom) {
                    Text(
                        fmtNum(data.priceValue, 6),
                        style = MaterialTheme.typography.displaySmall,
                        color = AionColors.Text,
                    )
                    Spacer(Modifier.width(12.dp))
                    Num(
                        fmtPct(data.changeValue, signed = true),
                        style = MaterialTheme.typography.titleMedium,
                        color = pnlColor(data.changeValue),
                        modifier = Modifier.padding(bottom = 5.dp),
                    )
                }
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                    Column {
                        Text("标记价格", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                        Num(fmtNum(data.markValue, 6), style = MaterialTheme.typography.bodyMedium)
                    }
                    Column {
                        Text("24h 成交额", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                        Num(
                            fmtNum(data.ticker?.quoteVolume24h ?: data.ticker?.volume24h, 2),
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    Column {
                        Text("更新时间", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                        Num(formatTime(data.timeValue), style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
        }
    }
}

@Composable
private fun CandleChart(series: Loadable<CandleSeries>, onRetry: () -> Unit) {
    val data = series.data
    when {
        data == null && series.error != null -> ErrorBanner(series.error, onRetry)
        data == null -> {
            SkeletonBlock(150.dp)
        }
        else -> {
            val closes = data.data.mapNotNull { it.close?.toBigDecimalOrNull() }
            val floats = closes.map { it.toFloat() }
            if (floats.size < 2) {
                EmptyState(
                    title = "暂无 K 线数据",
                    message = "${data.interval.ifBlank { "该周期" }} K 线正在同步，请稍后刷新。",
                    icon = Icons.Outlined.ShowChart,
                )
            } else {
                val first = closes.first()
                val last = closes.last()
                val rising = last >= first
                val lineColor = if (rising) AionColors.Green else AionColors.Red
                PriceLineChart(
                    values = floats,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(170.dp),
                    color = lineColor,
                )
                Spacer(Modifier.height(10.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("区间涨跌", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                        Num(
                            fmtPct(
                                if (first.signum() == 0) null
                                else last.subtract(first)
                                    .divide(first, 8, RoundingMode.HALF_UP)
                                    .multiply(BigDecimal(100))
                                    .toPlainString(),
                                signed = true,
                            ),
                            style = MaterialTheme.typography.bodyMedium,
                            color = if (rising) AionColors.Green else AionColors.Red,
                        )
                    }
                    Column(modifier = Modifier.weight(1f)) {
                        Text("最低 / 最高", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                        Num(
                            "${fmtNum(closes.minOrNull()?.toPlainString())} / ${fmtNum(closes.maxOrNull()?.toPlainString())}",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    Column(horizontalAlignment = Alignment.End) {
                        Text("K 线数", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                        Num("${floats.size}", style = MaterialTheme.typography.bodyMedium)
                    }
                }
                Spacer(Modifier.height(6.dp))
                Text(
                    "来源：${data.source ?: "backend"} · 最新 ${formatTime(data.data.lastOrNull()?.openTime)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                )
            }
        }
    }
}

@Composable
private fun SymbolRow(symbol: SymbolDto, selected: Boolean, onClick: () -> Unit) {
    val shape = RoundedCornerShape(14.dp)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(if (selected) AionColors.Primary.copy(alpha = 0.10f) else AionColors.Card)
            .border(
                1.dp,
                if (selected) AionColors.Primary.copy(alpha = 0.45f) else AionColors.Border.copy(alpha = 0.75f),
                shape,
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    symbol.symbol,
                    style = MaterialTheme.typography.titleMedium.copy(fontFamily = FontFamily.Monospace),
                    color = AionColors.Text,
                )
                Spacer(Modifier.width(6.dp))
                if (!symbol.isAllowed) {
                    Pill("未允许", AionColors.Amber)
                }
            }
            Text(
                "${symbol.baseAsset ?: "—"} / ${symbol.quoteAsset ?: "—"} · ${symbol.contractType ?: "FUTURES"}",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(
                "max ${symbol.maxLeverage ?: "—"}x",
                style = MaterialTheme.typography.labelMedium,
                color = AionColors.Muted,
            )
            Text(
                "min ${fmtNum(symbol.minNotional, 2)}",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
        Spacer(Modifier.width(10.dp))
        Pill(symbol.status ?: "—", if (symbol.status == "TRADING") AionColors.Green else AionColors.Muted)
    }
}
