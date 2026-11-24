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
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import dev.aion.app.AppContainer
import dev.aion.app.data.LearningPerformanceDto
import dev.aion.app.data.LessonDto
import dev.aion.app.data.LessonListDto
import dev.aion.app.data.Loadable
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.fmtFixed
import dev.aion.app.ui.fmtNum
import dev.aion.app.ui.fmtSignedUsd
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.pnlColor
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

class LearningViewModel(container: AppContainer) : BaseViewModel(container) {

    val performance = MutableStateFlow(Loadable<LearningPerformanceDto>())
    val lessons = MutableStateFlow(Loadable<LessonListDto>())

    init {
        refresh()
    }

    fun refresh() {
        load(performance) { container.repository.learningPerformance() }
        load(lessons) { container.repository.learningLessons() }
    }

    fun toggleLesson(id: String) {
        viewModelScope.launch {
            when (container.repository.toggleLesson(id)) {
                is dev.aion.app.data.ApiResult.Success -> refresh()
                else -> Unit
            }
        }
    }
}

@Composable
fun LearningScreen(container: AppContainer) {
    val vm: LearningViewModel = aionViewModel(container) { LearningViewModel(it) }
    val perf by vm.performance.collectAsState()
    val lessons by vm.lessons.collectAsState()

    GlobalRefreshEffect(container) { vm.refresh() }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            AionCard {
                SectionHeader(title = "学习与进化", subtitle = "AION 持续总结每笔交易，让 AI 不断变强")
                Spacer(Modifier.height(10.dp))
                when {
                    perf.data == null && perf.error != null ->
                        ErrorBanner(perf.error ?: "", { vm.refresh() })
                    perf.data == null -> SkeletonBlock(96.dp)
                    else -> {
                        val p = perf.data!!
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            StatTile("交易数", "${p.totalTrades}", Modifier.weight(1f))
                            StatTile(
                                "胜率",
                                p.winRate?.let { fmtFixed((it * 100).toString(), 1) + "%" } ?: "—",
                                Modifier.weight(1f),
                            )
                        }
                        Spacer(Modifier.height(10.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            StatTile("平均R", p.avgRMultiple?.let { fmtNum(it, 2) } ?: "—", Modifier.weight(1f))
                            StatTile(
                                "总盈亏",
                                fmtSignedUsd(p.totalPnlUsd),
                                Modifier.weight(1f),
                                valueColor = pnlColor(p.totalPnlUsd),
                            )
                        }
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "今日盈亏 ${fmtSignedUsd(p.todayPnlUsd)}",
                            style = MaterialTheme.typography.labelSmall,
                            color = pnlColor(p.todayPnlUsd),
                        )
                    }
                }
            }
        }

        item {
            AionCard {
                val activeCount = lessons.data?.items?.count { it.active } ?: 0
                SectionHeader(title = "AI 已学会的经验", subtitle = "$activeCount 条生效中 · 每 5 笔新结果自动提炼")
                Spacer(Modifier.height(8.dp))
                when {
                    lessons.data == null && lessons.error != null ->
                        ErrorBanner(lessons.error ?: "", { vm.refresh() })
                    lessons.data == null -> SkeletonBlock(72.dp)
                    lessons.data!!.items.isEmpty() -> Text(
                        "暂无经验。完成至少 5 笔模拟交易后会自动提炼。",
                        style = MaterialTheme.typography.bodySmall,
                        color = AionColors.Muted,
                    )
                    else -> lessons.data!!.items.forEach { lesson ->
                        LessonRow(lesson, onToggle = { vm.toggleLesson(lesson.id) })
                    }
                }
            }
        }

        perf.data?.let { p ->
            if (p.bySymbol.isNotEmpty()) {
                item {
                    AionCard {
                        SectionHeader(title = "按交易对统计")
                        Spacer(Modifier.height(8.dp))
                        p.bySymbol.entries.sortedBy { it.key }.forEach { (symbol, stat) ->
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(symbol, style = MaterialTheme.typography.titleMedium)
                                Spacer(Modifier.weight(1f))
                                Text(
                                    "${stat.trades} 笔 · 胜率 ${stat.winRate?.let { fmtFixed((it * 100).toString(), 0) + "%" } ?: "—"}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AionColors.Muted,
                                )
                                Spacer(Modifier.width(8.dp))
                                Text(
                                    fmtSignedUsd(stat.pnlUsd),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = pnlColor(stat.pnlUsd),
                                )
                            }
                        }
                    }
                }
            }

            item {
                AionCard {
                    SectionHeader(title = "最近交易结果")
                    Spacer(Modifier.height(8.dp))
                    if (p.recentOutcomes.isEmpty()) {
                        Text("暂无交易结果", style = MaterialTheme.typography.bodySmall, color = AionColors.Muted)
                    } else {
                        p.recentOutcomes.forEach { o ->
                            Column(modifier = Modifier.padding(vertical = 6.dp)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(
                                        "${o.symbol} ${o.positionSide}",
                                        style = MaterialTheme.typography.titleMedium,
                                    )
                                    Spacer(Modifier.weight(1f))
                                    Text(
                                        fmtSignedUsd(o.pnlUsd) +
                                            (o.rMultiple?.let { " (${fmtNum(it, 2)}R)" } ?: ""),
                                        style = MaterialTheme.typography.titleMedium,
                                        color = pnlColor(o.pnlUsd),
                                    )
                                }
                                Text(
                                    "${o.exitReason} · ${o.regime ?: "—"} · ${fmtNum(o.entryPrice, 4)} → ${fmtNum(o.exitPrice, 4)} · ${formatTime(o.closedAt)}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AionColors.Muted,
                                )
                            }
                            HorizontalDivider(color = AionColors.Border.copy(alpha = 0.4f))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun StatTile(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    valueColor: androidx.compose.ui.graphics.Color = AionColors.Text,
) {
    Column(
        modifier = modifier
            .background(AionColors.Bg, androidx.compose.foundation.shape.RoundedCornerShape(12.dp))
            .padding(12.dp),
    ) {
        Text(label, style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
        Spacer(Modifier.height(4.dp))
        Text(value, style = MaterialTheme.typography.titleLarge, color = valueColor)
    }
}

@Composable
private fun LessonRow(lesson: LessonDto, onToggle: () -> Unit) {
    Column(modifier = Modifier.padding(vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pill(
                if (lesson.active) "生效中" else "已停用",
                if (lesson.active) AionColors.Green else AionColors.Muted,
            )
            Spacer(Modifier.weight(1f))
            TextButton(onClick = onToggle) {
                Text(if (lesson.active) "停用" else "启用")
            }
        }
        Spacer(Modifier.height(4.dp))
        Text(lesson.lesson, style = MaterialTheme.typography.bodySmall, color = AionColors.Text)
        Spacer(Modifier.height(2.dp))
        Text(
            "${lesson.category} · 基于 ${lesson.outcomesCount} 笔结果 · ${formatTime(lesson.createdAt)}",
            style = MaterialTheme.typography.labelSmall,
            color = AionColors.Muted,
        )
        Spacer(Modifier.height(6.dp))
        HorizontalDivider(color = AionColors.Border.copy(alpha = 0.4f))
    }
}
