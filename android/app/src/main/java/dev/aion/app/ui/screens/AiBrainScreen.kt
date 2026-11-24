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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.SmartToy
import androidx.compose.material3.BottomSheetDefaults
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import dev.aion.app.AppContainer
import dev.aion.app.data.AgentRunDto
import dev.aion.app.data.AgentRunsResponse
import dev.aion.app.data.ApiResult
import dev.aion.app.data.Loadable
import dev.aion.app.data.warningTexts
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.AionField
import dev.aion.app.ui.components.EmptyState
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.InfoBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.LabelText
import dev.aion.app.ui.components.Num
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.formatTimeFull
import dev.aion.app.ui.statusColor
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

class AiBrainViewModel(container: AppContainer) : BaseViewModel(container) {

    val runs = MutableStateFlow(Loadable<AgentRunsResponse>())
    val selected = MutableStateFlow<AgentRunDto?>(null)
    val search = MutableStateFlow("")
    val symbol = MutableStateFlow("BTCUSDT")
    val running = MutableStateFlow(false)
    val triggerError = MutableStateFlow<String?>(null)

    init {
        refresh()
    }

    fun refresh() {
        load(runs) { container.repository.agentRuns(50) }
    }

    fun updateSymbol(value: String) {
        symbol.value = value.uppercase()
    }

    /** 触发一次真实分析；轮询到终态后自动打开结果。不会下单。 */
    fun trigger() {
        if (running.value) return
        val target = symbol.value.trim().uppercase()
        if (target.length < 3) {
            triggerError.value = "请输入交易对，例如 BTCUSDT"
            return
        }
        triggerError.value = null
        running.value = true
        viewModelScope.launch {
            try {
                when (val accepted = container.repository.analyze(target, "15m")) {
                    is ApiResult.Failure -> triggerError.value = accepted.userMessage()
                    is ApiResult.Success -> {
                        repeat(60) {
                            delay(5_000)
                            when (val detail = container.repository.agentRun(accepted.data.runId)) {
                                is ApiResult.Failure -> {
                                    triggerError.value = detail.userMessage()
                                    return@launch
                                }
                                is ApiResult.Success -> {
                                    if (detail.data.status != "RUNNING") {
                                        selected.value = detail.data
                                        refresh()
                                        return@launch
                                    }
                                }
                            }
                        }
                        triggerError.value = "分析仍在运行，请稍后下拉刷新查看结果"
                    }
                }
            } finally {
                running.value = false
            }
        }
    }

    fun select(run: AgentRunDto) {
        selected.value = run
    }

    fun clearSelection() {
        selected.value = null
    }

    fun updateSearch(value: String) {
        search.value = value
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AiBrainScreen(container: AppContainer) {
    val vm: AiBrainViewModel = aionViewModel(container) { AiBrainViewModel(it) }
    val runsState by vm.runs.collectAsState()
    val selected by vm.selected.collectAsState()
    val search by vm.search.collectAsState()
    val symbol by vm.symbol.collectAsState()
    val running by vm.running.collectAsState()
    val triggerError by vm.triggerError.collectAsState()

    GlobalRefreshEffect(container) { vm.refresh() }

    val allRuns = runsState.data?.items.orEmpty()
    val filtered = if (search.isBlank()) {
        allRuns
    } else {
        allRuns.filter {
            (it.symbol ?: "").contains(search, ignoreCase = true) ||
                (it.summary ?: "").contains(search, ignoreCase = true) ||
                it.title.contains(search, ignoreCase = true)
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
                SectionHeader(
                    title = "六个 AI 协力",
                    subtitle = "每个决定都由 6 个 Agent 依次完成，反方风控一票否决",
                )
                Spacer(Modifier.height(10.dp))
                SIX_AGENTS.forEachIndexed { index, agent ->
                    if (index > 0) {
                        HorizontalDivider(color = AionColors.Border, modifier = Modifier.padding(vertical = 8.dp))
                    }
                    Row(verticalAlignment = Alignment.Top) {
                        Pill("${index + 1}", if (agent.veto) AionColors.Red else AionColors.Primary)
                        Spacer(Modifier.width(10.dp))
                        Column(modifier = Modifier.weight(1f)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(agent.title, style = MaterialTheme.typography.titleMedium)
                                Spacer(Modifier.width(6.dp))
                                Pill(agent.stage, AionColors.Muted)
                                if (agent.veto) {
                                    Spacer(Modifier.width(6.dp))
                                    Pill("一票否决", AionColors.Red)
                                }
                            }
                            Spacer(Modifier.height(2.dp))
                            Text(
                                agent.purpose,
                                style = MaterialTheme.typography.bodySmall,
                                color = AionColors.Muted,
                            )
                        }
                    }
                }
            }
        }

        item {
            AionCard {
                SectionHeader(
                    title = "AI 决策记录",
                    subtitle = "来源 /agents/runs · 展示 Agent 产出的摘要与警告",
                )
                Spacer(Modifier.height(10.dp))
                AionField(
                    value = search,
                    onValueChange = { vm.updateSearch(it) },
                    label = "搜索摘要 / 交易对 / Agent",
                    keyboardType = KeyboardType.Text,
                )
                Spacer(Modifier.height(10.dp))
                AionField(
                    value = symbol,
                    onValueChange = { vm.updateSymbol(it) },
                    label = "交易对（运行分析用）",
                    placeholder = "BTCUSDT",
                    mono = true,
                    enabled = !running,
                )
                Spacer(Modifier.height(10.dp))
                Button(
                    onClick = { vm.trigger() },
                    enabled = !running,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(48.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = AionColors.Primary,
                        contentColor = AionColors.Bg,
                    ),
                ) {
                    if (running) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = AionColors.Bg,
                        )
                        Spacer(Modifier.width(10.dp))
                        Text("分析中… 6 个 Agent 协作")
                    } else {
                        Text("运行 AI 分析")
                    }
                }
                triggerError?.let {
                    Spacer(Modifier.height(10.dp))
                    InfoBanner(it, AionColors.Red)
                }
                if (runsState.error != null && runsState.data != null) {
                    Spacer(Modifier.height(10.dp))
                    ErrorBanner(runsState.error ?: "", { vm.refresh() })
                }
            }
        }

        when {
            runsState.data == null && runsState.error != null -> item {
                AionCard {
                    ErrorBanner(runsState.error ?: "", { vm.refresh() })
                    Spacer(Modifier.height(10.dp))
                    InfoBanner(
                        "还没有 AI 分析记录。发起一次合议后，这里会显示完整过程。",
                        AionColors.Amber,
                    )
                }
            }

            runsState.data == null -> item {
                SkeletonBlock(110.dp)
                Spacer(Modifier.height(10.dp))
                SkeletonBlock(110.dp)
                Spacer(Modifier.height(10.dp))
                SkeletonBlock(110.dp)
            }

            allRuns.isEmpty() -> item {
                AionCard(padding = PaddingValues(0.dp)) {
                    EmptyState(
                        title = "暂无分析记录",
                        message = "AI 运行后，这里会显示摘要与置信度。",
                        icon = Icons.Outlined.SmartToy,
                    )
                }
            }

            filtered.isEmpty() -> item {
                AionCard(padding = PaddingValues(0.dp)) {
                    EmptyState(
                        title = "没有匹配的记录",
                        message = "调整搜索关键字后再试。",
                        icon = Icons.Outlined.SmartToy,
                    )
                }
            }

            else -> items(filtered, key = { it.identifier ?: it.createdAt ?: it.summary ?: it.hashCode().toString() }) { run ->
                AiRunCard(run) { vm.select(run) }
            }
        }

        item { Spacer(Modifier.height(4.dp)) }
    }

    selected?.let { run ->
        ModalBottomSheet(
            onDismissRequest = { vm.clearSelection() },
            containerColor = AionColors.CardHigh,
            contentColor = AionColors.Text,
            dragHandle = { BottomSheetDefaults.DragHandle() },
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(start = 20.dp, end = 20.dp, bottom = 28.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(run.title, style = MaterialTheme.typography.titleLarge)
                    Spacer(Modifier.width(8.dp))
                    run.status?.let { Pill(it, statusColor(it), filled = true) }
                }
                Spacer(Modifier.height(4.dp))
                Num(
                    listOfNotNull(run.symbol, run.interval).joinToString(" · ").ifBlank { "—" },
                    style = MaterialTheme.typography.bodyMedium,
                    color = AionColors.Muted,
                )
                Spacer(Modifier.height(14.dp))

                LabelText("摘要")
                Spacer(Modifier.height(4.dp))
                Text(
                    run.summary?.takeIf { it.isNotBlank() } ?: "服务端未提供摘要。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = AionColors.Text,
                )

                Spacer(Modifier.height(16.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                    Column {
                        LabelText("置信度")
                        Num(run.confidenceText ?: "—", style = MaterialTheme.typography.titleMedium)
                    }
                    Column {
                        LabelText("推荐动作")
                        Text(
                            run.actionText ?: "—",
                            style = MaterialTheme.typography.bodyMedium,
                            color = AionColors.Primary,
                        )
                    }
                }

                val warnings = run.warningTexts()
                if (warnings.isNotEmpty()) {
                    Spacer(Modifier.height(16.dp))
                    LabelText("警告")
                    Spacer(Modifier.height(6.dp))
                    warnings.forEach { warning ->
                        InfoBanner(warning, AionColors.Amber)
                        Spacer(Modifier.height(6.dp))
                    }
                }

                val steps = run.messages.orEmpty()
                if (steps.isNotEmpty()) {
                    Spacer(Modifier.height(16.dp))
                    LabelText("Agent 步骤")
                    Spacer(Modifier.height(6.dp))
                    steps.forEach { step ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                step.agentName ?: step.role ?: "agent",
                                style = MaterialTheme.typography.bodyMedium,
                                modifier = Modifier.weight(1f),
                            )
                            step.status?.let { Pill(it, statusColor(it)) }
                        }
                        Text(
                            step.summary?.takeIf { it.isNotBlank() } ?: "—",
                            style = MaterialTheme.typography.bodySmall,
                            color = AionColors.Muted,
                            maxLines = 4,
                        )
                        Spacer(Modifier.height(8.dp))
                    }
                }

                if (run.error != null) {
                    Spacer(Modifier.height(12.dp))
                    InfoBanner(run.error ?: "", AionColors.Red)
                }

                Spacer(Modifier.height(16.dp))
                HorizontalDivider(color = AionColors.Border)
                Spacer(Modifier.height(8.dp))
                KeyValueRow("Run ID", run.identifier ?: "—")
                KeyValueRow("Agent", run.title)
                KeyValueRow("类型", run.runType ?: run.type ?: "—")
                KeyValueRow("开始", formatTimeFull(run.startedAt ?: run.createdAt))
                KeyValueRow("结束", formatTimeFull(run.finishedAt))
                run.verdict?.let { KeyValueRow("结论", it) }
                run.modelName?.let { KeyValueRow("模型", listOfNotNull(run.provider, it).joinToString(" / ")) }
                run.totalCostUsd?.let { KeyValueRow("成本 USD", it) }
                if (run.confidenceText != null) {
                    KeyValueRow("confidence", run.confidenceText ?: "—")
                }
            }
        }
    }
}

@Composable
private fun AiRunCard(run: AgentRunDto, onClick: () -> Unit) {
    AionCard(onClick = onClick) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        run.title,
                        style = MaterialTheme.typography.titleMedium,
                        maxLines = 1,
                    )
                    Spacer(Modifier.width(6.dp))
                    run.symbol?.let { Pill(it, AionColors.Primary) }
                }
                Text(
                    listOfNotNull(run.interval, formatTime(run.createdAt ?: run.startedAt))
                        .joinToString(" · "),
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                run.status?.let { Pill(it, statusColor(it), filled = true) }
                val confidence = run.confidenceText
                if (confidence != null) {
                    Text(
                        "conf $confidence",
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                        color = AionColors.Muted,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        Text(
            run.summary?.takeIf { it.isNotBlank() } ?: "服务端未提供摘要。",
            style = MaterialTheme.typography.bodySmall,
            color = AionColors.Text,
            maxLines = 3,
        )
        val warningCount = run.warningTexts().size
        if (warningCount > 0) {
            Spacer(Modifier.height(8.dp))
            Pill("$warningCount 条警告", AionColors.Amber)
        }
    }
}

private data class AgentBrief(val title: String, val stage: String, val purpose: String, val veto: Boolean = false)

private val SIX_AGENTS = listOf(
    AgentBrief("市场状态分析师", "感知", "判断趋势 / 震荡 / 高波动，决定其他 AI 该用哪套打法。"),
    AgentBrief("技术面分析师", "感知", "读 K 线、均线、量价与关键支撑阻力，给出入场位置。"),
    AgentBrief("衍生品数据分析师", "感知", "解读资金费率、持仓量与多空比，识别拥挤和逼空风险。"),
    AgentBrief("策略研究员", "研判", "结合历史经验与当前行情，提出可执行的策略与参数。"),
    AgentBrief("首席决策官", "决策", "汇总前四个 AI，输出做多 / 做空 / 观望、仓位、止损与止盈。"),
    AgentBrief("反方风控官", "风控", "专门挑刺，拥有一票否决权，防止追高、扛单与过度交易。", veto = true),
)
