package dev.aion.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AccountBalanceWallet
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import dev.aion.app.AppContainer
import dev.aion.app.data.Loadable
import dev.aion.app.data.PositionDto
import dev.aion.app.data.PositionsResponse
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.EmptyState
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.InfoBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.LoadableContent
import dev.aion.app.ui.components.ModePill
import dev.aion.app.ui.components.Num
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.fmtNum
import dev.aion.app.ui.fmtPct
import dev.aion.app.ui.fmtSignedUsd
import dev.aion.app.ui.fmtUsd
import dev.aion.app.ui.pnlColor
import dev.aion.app.ui.sideColor
import dev.aion.app.ui.toDecimal
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import java.math.BigDecimal

class PositionsViewModel(container: AppContainer) : BaseViewModel(container) {

    val positions = MutableStateFlow(Loadable<PositionsResponse>())

    init {
        refresh()
        viewModelScope.launch {
            container.realtime.events.collect { event ->
                val type = event.type.lowercase()
                if (type.contains("position") || type.contains("account") || type.contains("order")) {
                    load(positions) { container.repository.positions() }
                }
            }
        }
    }

    fun refresh() {
        load(positions) { container.repository.positions() }
    }
}

@Composable
fun PositionsScreen(container: AppContainer, onNavigate: (String) -> Unit) {
    val vm: PositionsViewModel = aionViewModel(container) { PositionsViewModel(it) }
    val state by vm.positions.collectAsState()
    val system by container.appState.system.collectAsState()

    GlobalRefreshEffect(container) { vm.refresh() }
    LaunchedEffect(Unit) {
        while (true) {
            delay(15_000)
            vm.refresh()
        }
    }

    val all = state.data?.all.orEmpty()
    val totalPnl: BigDecimal? = if (state.data == null || all.isEmpty()) {
        null
    } else {
        all.fold(BigDecimal.ZERO) { acc, position ->
            acc + (toDecimal(position.unrealizedPnl) ?: BigDecimal.ZERO)
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
            AionCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("持仓概览", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.width(8.dp))
                    ModePill(state.data?.mode ?: system?.mode)
                    if (state.data?.stale == true) {
                        Spacer(Modifier.width(6.dp))
                        Pill("数据过期", AionColors.Amber)
                    }
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { onNavigate("orders") }) {
                        Text("订单", color = AionColors.Primary, style = MaterialTheme.typography.labelMedium)
                    }
                }
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                    Column {
                        Text("未实现盈亏合计", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                        Num(
                            if (totalPnl == null) "—" else fmtSignedUsd(totalPnl.toPlainString()),
                            style = MaterialTheme.typography.titleLarge,
                            color = if (totalPnl == null) AionColors.Text else pnlColor(totalPnl.toPlainString()),
                            weight = FontWeight.SemiBold,
                        )
                    }
                    Column {
                        Text("持仓数量", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                        Num(
                            if (state.data == null) "—" else all.size.toString(),
                            style = MaterialTheme.typography.titleLarge,
                            color = AionColors.Text,
                        )
                    }
                }
                if (state.error != null && state.data != null) {
                    Spacer(Modifier.height(10.dp))
                    ErrorBanner(state.error ?: "", { vm.refresh() })
                }
            }
        }

        item {
            LoadableContent(
                state = state,
                onRetry = { vm.refresh() },
                isEmpty = { it.all.isEmpty() },
                emptyTitle = "暂无持仓",
                emptyMessage = "当前没有持仓。",
                emptyIcon = Icons.Outlined.AccountBalanceWallet,
            ) { data ->
                Column {
                    if (data.stale) {
                        InfoBanner(
                            "该持仓数据正在同步，价格与盈亏可能略有延迟。",
                            AionColors.Amber,
                        )
                        Spacer(Modifier.height(10.dp))
                    }
                }
            }
        }

        items(all, key = { it.symbol + it.positionSide }) { position ->
            PositionCard(position)
        }

        item { Spacer(Modifier.height(4.dp)) }
    }
}

@Composable
private fun PositionCard(position: PositionDto) {
    AionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        position.symbol,
                        style = MaterialTheme.typography.titleMedium.copy(fontFamily = FontFamily.Monospace),
                    )
                    Spacer(Modifier.width(6.dp))
                    Pill(position.positionSide, sideColor(position.positionSide), filled = true)
                    Spacer(Modifier.width(6.dp))
                    Text(
                        "${position.leverage}x · ${position.marginMode ?: "—"}",
                        style = MaterialTheme.typography.labelSmall,
                        color = AionColors.Muted,
                    )
                }
                Num(
                    "名义 ${fmtUsd(position.notionalUsd)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                Num(
                    fmtSignedUsd(position.unrealizedPnl),
                    style = MaterialTheme.typography.titleLarge,
                    color = pnlColor(position.unrealizedPnl),
                    weight = FontWeight.SemiBold,
                )
                Num(
                    fmtPct(position.unrealizedPnlPct, signed = true),
                    style = MaterialTheme.typography.labelSmall,
                    color = pnlColor(position.unrealizedPnl),
                )
            }
        }

        Spacer(Modifier.height(12.dp))
        HorizontalDivider(color = AionColors.Border.copy(alpha = 0.6f))
        Spacer(Modifier.height(6.dp))

        Row(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.weight(1f)) {
                KeyValueRow("数量", fmtNum(position.quantity, 6))
                KeyValueRow("开仓价", fmtNum(position.entryPrice))
            }
            Spacer(Modifier.width(14.dp))
            Column(modifier = Modifier.weight(1f)) {
                KeyValueRow("标记价", fmtNum(position.markPrice))
                KeyValueRow("强平价", fmtNum(position.liquidationPrice))
            }
        }

        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "保护单",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
                modifier = Modifier.weight(1f),
            )
            if (position.hasStopLoss) {
                Pill("SL ${fmtNum(position.stopLossPrice)}", AionColors.Green)
            } else {
                Pill("无止损", AionColors.Red)
            }
            Spacer(Modifier.width(6.dp))
            if (position.hasTakeProfit) {
                Pill("TP ${fmtNum(position.takeProfitPrice)}", AionColors.Green)
            } else {
                Pill("无止盈", AionColors.Muted)
            }
            if (position.protectionVerified) {
                Spacer(Modifier.width(6.dp))
                Pill("已校验", AionColors.Primary)
            }
        }

        if (!position.hasStopLoss) {
            Spacer(Modifier.height(8.dp))
            InfoBanner(
                "该持仓没有止损保护，价格反向波动时风险敞口不受控。",
                AionColors.Red,
            )
        }
    }
}
