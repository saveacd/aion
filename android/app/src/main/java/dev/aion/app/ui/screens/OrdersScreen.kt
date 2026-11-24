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
import androidx.compose.material.icons.outlined.ReceiptLong
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import dev.aion.app.AppContainer
import dev.aion.app.data.FillDto
import dev.aion.app.data.FillsResponse
import dev.aion.app.data.Loadable
import dev.aion.app.data.OrderDto
import dev.aion.app.data.OrdersResponse
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.EmptyState
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.Num
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.PillTabs
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.fmtNum
import dev.aion.app.ui.fmtSignedUsd
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.pnlColor
import dev.aion.app.ui.sideColor
import dev.aion.app.ui.statusColor
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import java.util.Locale

class OrdersViewModel(container: AppContainer) : BaseViewModel(container) {

    val orders = MutableStateFlow(Loadable<OrdersResponse>())
    val fills = MutableStateFlow(Loadable<FillsResponse>())

    init {
        refresh()
    }

    fun refresh() {
        load(orders) { container.repository.orders(100) }
        load(fills) { container.repository.fills(100) }
    }
}

private val terminalStatuses = setOf(
    "FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "FAILED",
)

@Composable
fun OrdersScreen(container: AppContainer) {
    val vm: OrdersViewModel = aionViewModel(container) { OrdersViewModel(it) }
    val ordersState by vm.orders.collectAsState()
    val fillsState by vm.fills.collectAsState()
    var tab by rememberSaveable { mutableIntStateOf(0) }

    GlobalRefreshEffect(container) { vm.refresh() }
    LaunchedEffect(Unit) {
        while (true) {
            delay(10_000)
            vm.refresh()
        }
    }

    val allOrders = ordersState.data?.items.orEmpty()
    val current = allOrders.filter { it.status.uppercase(Locale.US) !in terminalStatuses }
    val history = allOrders.filter { it.status.uppercase(Locale.US) in terminalStatuses }
    val fills = fillsState.data?.items.orEmpty()

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PillTabs(
                labels = listOf("当前委托 ${current.size}", "历史 ${history.size}", "成交 ${fills.size}"),
                selected = tab,
                onSelect = { tab = it },
            )
        }

        when (tab) {
            0 -> {
                if (ordersState.data == null) {
                    item { OrdersPlaceholder(ordersState.error, vm::refresh) }
                } else if (current.isEmpty()) {
                    item {
                        AionCard(padding = PaddingValues(0.dp)) {
                            EmptyState(
                                title = "没有当前委托",
                                message = "所有委托都已进入终态，或尚未提交任何订单。",
                                icon = Icons.Outlined.ReceiptLong,
                            )
                        }
                    }
                } else {
                    if (ordersState.error != null) {
                        item { ErrorBanner(ordersState.error ?: "", vm::refresh) }
                    }
                    items(current, key = { orderKey(it) }) { OrderCard(it) }
                }
            }

            1 -> {
                if (ordersState.data == null) {
                    item { OrdersPlaceholder(ordersState.error, vm::refresh) }
                } else if (history.isEmpty()) {
                    item {
                        AionCard(padding = PaddingValues(0.dp)) {
                            EmptyState(
                                title = "暂无历史订单",
                                message = "暂时没有已成交或已取消的订单。",
                                icon = Icons.Outlined.ReceiptLong,
                            )
                        }
                    }
                } else {
                    items(history, key = { orderKey(it) }) { OrderCard(it) }
                }
            }

            else -> {
                if (fillsState.data == null) {
                    item { OrdersPlaceholder(fillsState.error, vm::refresh) }
                } else if (fills.isEmpty()) {
                    item {
                        AionCard(padding = PaddingValues(0.dp)) {
                            EmptyState(
                                title = "暂无成交记录",
                                message = "还没有从交易所同步到成交（fill）。",
                                icon = Icons.Outlined.ReceiptLong,
                            )
                        }
                    }
                } else {
                    if (fillsState.error != null) {
                        item { ErrorBanner(fillsState.error ?: "", vm::refresh) }
                    }
                    items(fills, key = { it.id ?: (it.symbol + it.tradedAt + it.price) }) { FillCard(it) }
                }
            }
        }

        item {
            Text(
                "当前委托/历史由客户端按状态划分；数据来源 /trading/orders 与 /trading/fills（各取最近 100 条）。",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
                modifier = Modifier.padding(top = 4.dp, bottom = 8.dp),
            )
        }
    }
}

private fun orderKey(order: OrderDto): String =
    order.id ?: order.clientOrderId ?: (order.symbol + order.createdAt + order.side + order.orderType)

@Composable
private fun OrdersPlaceholder(error: String?, onRetry: () -> Unit) {
    if (error != null) {
        ErrorBanner(error, onRetry)
    } else {
        Column {
            SkeletonBlock(76.dp)
            Spacer(Modifier.height(10.dp))
            SkeletonBlock(76.dp)
            Spacer(Modifier.height(10.dp))
            SkeletonBlock(76.dp)
        }
    }
}

@Composable
private fun OrderCard(order: OrderDto) {
    AionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        order.symbol,
                        style = MaterialTheme.typography.titleMedium.copy(fontFamily = FontFamily.Monospace),
                    )
                    Spacer(Modifier.width(6.dp))
                    Pill(order.side, sideColor(order.side), filled = true)
                    Spacer(Modifier.width(4.dp))
                    Pill(order.orderType, AionColors.Muted)
                    if (order.reduceOnly) {
                        Spacer(Modifier.width(4.dp))
                        Pill("reduce-only", AionColors.Purple)
                    }
                }
                Text(
                    "${order.purpose ?: "ORDER"} · ${formatTime(order.createdAt)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
            Pill(order.status, statusColor(order.status), filled = true)
        }
        Spacer(Modifier.height(10.dp))
        HorizontalDivider(color = AionColors.Border.copy(alpha = 0.6f))
        Spacer(Modifier.height(4.dp))
        Row(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.weight(1f)) {
                KeyValueRow("数量", fmtNum(order.quantity, 6))
                KeyValueRow("委托价", fmtNum(order.price))
            }
            Spacer(Modifier.width(14.dp))
            Column(modifier = Modifier.weight(1f)) {
                KeyValueRow("已成交", fmtNum(order.filledQuantity, 6))
                KeyValueRow("均价", fmtNum(order.avgFillPrice))
            }
        }
        if (order.errorMessage != null) {
            Spacer(Modifier.height(8.dp))
            Text(
                "${order.errorCode ?: "ERROR"} · ${order.errorMessage}",
                style = MaterialTheme.typography.bodySmall,
                color = AionColors.Red,
            )
        }
        if (order.submitAttempts > 1) {
            Spacer(Modifier.height(4.dp))
            Text(
                "提交尝试 ${order.submitAttempts} 次",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
            )
        }
    }
}

@Composable
private fun FillCard(fill: FillDto) {
    AionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        fill.symbol,
                        style = MaterialTheme.typography.titleMedium.copy(fontFamily = FontFamily.Monospace),
                    )
                    Spacer(Modifier.width(6.dp))
                    Pill(fill.side, sideColor(fill.side), filled = true)
                    Spacer(Modifier.width(4.dp))
                    Pill(if (fill.isMaker) "MAKER" else "TAKER", AionColors.Muted)
                }
                Text(
                    formatTime(fill.tradedAt),
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
            if (fill.realizedPnl != null) {
                Num(
                    fmtSignedUsd(fill.realizedPnl),
                    style = MaterialTheme.typography.titleMedium,
                    color = pnlColor(fill.realizedPnl),
                    weight = FontWeight.SemiBold,
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        Row(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.weight(1f)) {
                KeyValueRow("成交价", fmtNum(fill.price))
                KeyValueRow("数量", fmtNum(fill.quantity, 6))
            }
            Spacer(Modifier.width(14.dp))
            Column(modifier = Modifier.weight(1f)) {
                KeyValueRow("名义", fmtNum(fill.quoteQuantity, 2))
                KeyValueRow(
                    "手续费",
                    "${fmtNum(fill.commission, 6)} ${fill.commissionAsset ?: ""}".trim(),
                )
            }
        }
    }
}
