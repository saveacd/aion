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
import androidx.compose.material.icons.outlined.QueryStats
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import dev.aion.app.AppContainer
import dev.aion.app.data.Loadable
import dev.aion.app.data.StrategyDto
import dev.aion.app.data.StrategiesResponse
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.EmptyState
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.LabelText
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.statusColor
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.flow.MutableStateFlow

class StrategiesViewModel(container: AppContainer) : BaseViewModel(container) {

    val strategies = MutableStateFlow(Loadable<StrategiesResponse>())

    init {
        refresh()
    }

    fun refresh() {
        load(strategies) { container.repository.strategies() }
    }
}

@Composable
fun StrategiesScreen(container: AppContainer) {
    val vm: StrategiesViewModel = aionViewModel(container) { StrategiesViewModel(it) }
    val state by vm.strategies.collectAsState()

    GlobalRefreshEffect(container) { vm.refresh() }

    val items = state.data?.items.orEmpty()

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            AionCard {
                SectionHeader(
                    title = "策略",
                    subtitle = "只读视图 · 来源 /strategies",
                )
                if (state.data != null) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "共 ${items.size} 个策略，客户端不提供启停操作；状态变更请在服务端完成。",
                        style = MaterialTheme.typography.labelSmall,
                        color = AionColors.Muted,
                    )
                }
                if (state.error != null && state.data != null) {
                    Spacer(Modifier.height(10.dp))
                    ErrorBanner(state.error ?: "", { vm.refresh() })
                }
            }
        }

        when {
            state.data == null && state.error != null -> item {
                AionCard { ErrorBanner(state.error ?: "", { vm.refresh() }) }
            }
            state.data == null -> item {
                SkeletonBlock(96.dp)
                Spacer(Modifier.height(10.dp))
                SkeletonBlock(96.dp)
            }
            items.isEmpty() -> item {
                AionCard(padding = PaddingValues(0.dp)) {
                    EmptyState(
                        title = "暂无策略",
                        message = "暂时没有可用的策略。",
                        icon = Icons.Outlined.QueryStats,
                    )
                }
            }
            else -> items(items, key = { it.id ?: it.name }) { strategy ->
                StrategyCard(strategy)
            }
        }

        item { Spacer(Modifier.height(4.dp)) }
    }
}

@Composable
private fun StrategyCard(strategy: StrategyDto) {
    AionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text(strategy.name.ifBlank { "未命名策略" }, style = MaterialTheme.typography.titleMedium)
                Text(
                    "v${strategy.version ?: "—"} · ${strategy.interval.ifBlank { "—" }} · 更新 ${formatTime(strategy.updatedAt ?: strategy.createdAt)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                )
            }
            Pill(strategy.state.ifBlank { "UNKNOWN" }, statusColor(strategy.state), filled = true)
        }

        if (!strategy.description.isNullOrBlank()) {
            Spacer(Modifier.height(8.dp))
            Text(
                strategy.description ?: "",
                style = MaterialTheme.typography.bodySmall,
                color = AionColors.Text,
                maxLines = 3,
            )
        }

        if (strategy.symbols.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                strategy.symbols.take(5).forEach { symbol ->
                    Pill(symbol, AionColors.Primary)
                }
                if (strategy.symbols.size > 5) {
                    Pill("+${strategy.symbols.size - 5}", AionColors.Muted)
                }
            }
        }

        Spacer(Modifier.height(10.dp))
        HorizontalDivider(color = AionColors.Border.copy(alpha = 0.6f))
        Spacer(Modifier.height(4.dp))
        KeyValueRow("策略 ID", strategy.id ?: "—")
        KeyValueRow("创建者", strategy.createdBy ?: "—", mono = false)
        strategy.notes?.takeIf { it.isNotBlank() }?.let { notes ->
            Spacer(Modifier.height(6.dp))
            LabelText("备注")
            Text(notes, style = MaterialTheme.typography.bodySmall, color = AionColors.Muted)
        }
    }
}
