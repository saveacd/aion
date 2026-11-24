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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import dev.aion.app.AppContainer
import dev.aion.app.data.ApiResult
import dev.aion.app.data.ExchangeSettingDto
import dev.aion.app.data.ModelAssignmentDto
import dev.aion.app.data.SystemStatusDto
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.SessionViewModel
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.AionField
import dev.aion.app.ui.components.ConfirmDialog
import dev.aion.app.ui.components.InfoBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.ModePill
import dev.aion.app.ui.components.Num
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import java.util.Locale

data class ConnectionTest(
    val loading: Boolean = false,
    val success: Boolean = false,
    val message: String = "",
    val requestId: String? = null,
)

data class ExchangeUiState(
    val loading: Boolean = false,
    val saving: Boolean = false,
    val items: List<ExchangeSettingDto> = emptyList(),
    val configured: Map<String, Boolean> = emptyMap(),
    val message: String? = null,
    val messageOk: Boolean? = null,
)

class SettingsViewModel(container: AppContainer) : BaseViewModel(container) {

    val me = container.appState.me
    val system = container.appState.system
    val test = MutableStateFlow(ConnectionTest())
    val exchange = MutableStateFlow(ExchangeUiState())
    val assignment = MutableStateFlow<ModelAssignmentDto?>(null)

    init {
        loadExchange()
        loadAssignment()
    }

    fun testConnection() {
        if (test.value.loading) return
        test.value = ConnectionTest(loading = true)
        viewModelScope.launch {
            when (val result = container.repository.systemStatus()) {
                is ApiResult.Success -> {
                    container.repository.refreshMe()
                    test.value = ConnectionTest(
                        success = true,
                        message = "连接成功 · ${describe(result.data)}",
                    )
                }
                is ApiResult.Failure -> {
                    test.value = ConnectionTest(
                        success = false,
                        message = result.userMessage(),
                        requestId = result.requestId,
                    )
                }
            }
        }
    }

    fun loadExchange() {
        exchange.value = exchange.value.copy(loading = true)
        viewModelScope.launch {
            when (val result = container.repository.exchangeSettings()) {
                is ApiResult.Success -> exchange.value = exchange.value.copy(
                    loading = false,
                    items = result.data.items,
                    configured = result.data.configured,
                )
                is ApiResult.Failure -> exchange.value = exchange.value.copy(
                    loading = false,
                    message = result.userMessage(),
                    messageOk = false,
                )
            }
        }
    }

    fun saveExchange(mode: String, apiKey: String, apiSecret: String, verify: Boolean) {
        if (exchange.value.saving) return
        if (apiKey.isBlank() || apiSecret.isBlank()) {
            exchange.value = exchange.value.copy(message = "请填写 API Key 和 Secret", messageOk = false)
            return
        }
        exchange.value = exchange.value.copy(saving = true, message = null, messageOk = null)
        viewModelScope.launch {
            when (val result = container.repository.saveExchange(mode, apiKey, apiSecret, verify)) {
                is ApiResult.Success -> {
                    exchange.value = exchange.value.copy(
                        saving = false,
                        message = result.data.message,
                        messageOk = result.data.verified,
                    )
                    loadExchange()
                }
                is ApiResult.Failure -> exchange.value = exchange.value.copy(
                    saving = false,
                    message = result.userMessage(),
                    messageOk = false,
                )
            }
        }
    }

    fun deleteExchange(mode: String) {
        exchange.value = exchange.value.copy(saving = true, message = null)
        viewModelScope.launch {
            when (val result = container.repository.deleteExchange(mode)) {
                is ApiResult.Success -> {
                    exchange.value = exchange.value.copy(
                        saving = false,
                        message = "已删除 $mode 凭据",
                        messageOk = true,
                    )
                    loadExchange()
                }
                is ApiResult.Failure -> exchange.value = exchange.value.copy(
                    saving = false,
                    message = result.userMessage(),
                    messageOk = false,
                )
            }
        }
    }

    fun loadAssignment() {
        viewModelScope.launch {
            when (val result = container.repository.myModelAssignment()) {
                is ApiResult.Success -> assignment.value = result.data
                is ApiResult.Failure -> assignment.value = null
            }
        }
    }

    private fun describe(status: SystemStatusDto): String {
        val version = status.version ?: "unknown"
        val mode = status.mode ?: "unknown"
        return "v$version · $mode"
    }
}

@Composable
fun SettingsScreen(container: AppContainer, sessionVm: SessionViewModel) {
    val vm: SettingsViewModel = aionViewModel(container) { SettingsViewModel(it) }
    val me by vm.me.collectAsState()
    val system by vm.system.collectAsState()
    val test by vm.test.collectAsState()
    val exchange by vm.exchange.collectAsState()
    val assignment by vm.assignment.collectAsState()
    val loggingOut by sessionVm.loggingOut.collectAsState()

    var mode by remember { mutableStateOf("TESTNET") }
    var apiKey by remember { mutableStateOf("") }
    var apiSecret by remember { mutableStateOf("") }
    var showLogout by remember { mutableStateOf(false) }

    val saved = exchange.items.firstOrNull { it.mode == mode }
    val configured = exchange.configured[mode] == true

    if (showLogout) {
        ConfirmDialog(
            title = "退出登录",
            message = "将清除本机保存的 access/refresh token，并断开实时连接。",
            confirmLabel = "退出登录",
            destructive = true,
            onDismiss = { showLogout = false },
            onConfirm = {
                showLogout = false
                sessionVm.logout()
            },
        )
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg)
            .verticalScroll(rememberScrollState())
            .padding(PaddingValues(16.dp)),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        AionCard {
            SectionHeader(title = "连接", subtitle = "AION 云服务已自动连接")
            Spacer(Modifier.height(10.dp))
            Text("已连接", style = MaterialTheme.typography.bodyMedium, color = AionColors.Green)
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = { vm.testConnection() },
                enabled = !test.loading,
                colors = ButtonDefaults.buttonColors(containerColor = AionColors.Primary),
                shape = RoundedCornerShape(12.dp),
            ) {
                if (test.loading) {
                    CircularProgressIndicator(
                        modifier = Modifier
                            .padding(end = 8.dp)
                            .height(16.dp),
                        strokeWidth = 2.dp,
                        color = AionColors.Text,
                    )
                }
                Text(if (test.loading) "正在连接…" else "测试连接")
            }
            if (test.message.isNotBlank()) {
                Spacer(Modifier.height(10.dp))
                Pill(
                    if (test.success) "连接成功" else "连接失败",
                    if (test.success) AionColors.Green else AionColors.Red,
                    filled = true,
                )
                Spacer(Modifier.height(6.dp))
                Text(test.message, style = MaterialTheme.typography.bodySmall, color = AionColors.Text)
            }
        }

        AionCard {
            SectionHeader(title = "账户", subtitle = "当前登录账户")
            Spacer(Modifier.height(8.dp))
            KeyValueRow("用户名", me?.username ?: "—", mono = false)
            Row(
                modifier = Modifier.padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "角色",
                    style = MaterialTheme.typography.bodySmall,
                    color = AionColors.Muted,
                    modifier = Modifier.weight(1f),
                )
                Pill(if (me?.isAdmin == true) "ADMIN" else "USER", if (me?.isAdmin == true) AionColors.Primary else AionColors.Muted)
            }
            Row(
                modifier = Modifier.padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "两步验证",
                    style = MaterialTheme.typography.bodySmall,
                    color = AionColors.Muted,
                    modifier = Modifier.weight(1f),
                )
                Pill(if (me?.totpEnabled == true) "已启用" else "未启用", if (me?.totpEnabled == true) AionColors.Green else AionColors.Amber)
            }
            KeyValueRow("最近登录", formatTime(me?.lastLoginAt))
        }

        AionCard {
            SectionHeader(title = "对接 Binance API", subtitle = "密钥加密保存在 AION 云端，仅本人可用")
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                ModeButton("TESTNET", mode == "TESTNET") { mode = "TESTNET" }
                ModeButton("MAINNET", mode == "MAINNET") { mode = "MAINNET" }
            }
            Spacer(Modifier.height(6.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "$mode 凭据",
                    style = MaterialTheme.typography.bodySmall,
                    color = AionColors.Muted,
                    modifier = Modifier.weight(1f),
                )
                Pill(
                    if (configured) "已配置" else "未配置",
                    if (configured) AionColors.Green else AionColors.Amber,
                )
            }
            if (saved != null) {
                Text(
                    "Key ${if (saved.hasApiKey) "已保存" else "未保存"} · " +
                        "Secret ${if (saved.hasApiSecret) "已保存" else "未保存"}" +
                        (saved.lastCheckedAt?.let { " · 验证于 ${formatTime(it)}" } ?: ""),
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                )
            }
            Spacer(Modifier.height(12.dp))
            AionField(
                value = apiKey,
                onValueChange = { apiKey = it },
                label = "API Key",
                placeholder = "粘贴 Binance Futures API Key",
                mono = true,
                enabled = !exchange.saving,
            )
            Spacer(Modifier.height(10.dp))
            AionField(
                value = apiSecret,
                onValueChange = { apiSecret = it },
                label = "API Secret",
                enabled = !exchange.saving,
                visualTransformation = PasswordVisualTransformation(),
            )
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Button(
                    onClick = { vm.saveExchange(mode, apiKey.trim(), apiSecret.trim(), true) },
                    enabled = !exchange.saving,
                    colors = ButtonDefaults.buttonColors(containerColor = AionColors.Primary),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    if (exchange.saving) {
                        CircularProgressIndicator(
                            modifier = Modifier
                                .padding(end = 8.dp)
                                .height(16.dp),
                            strokeWidth = 2.dp,
                            color = AionColors.Text,
                        )
                    }
                    Text(if (exchange.saving) "保存中…" else "保存并验证")
                }
                Spacer(Modifier.width(10.dp))
                TextButton(
                    onClick = { vm.deleteExchange(mode) },
                    enabled = !exchange.saving && saved != null,
                ) {
                    Text("删除", color = AionColors.Red)
                }
            }
            if (exchange.message != null) {
                Spacer(Modifier.height(10.dp))
                InfoBanner(
                    exchange.message ?: "",
                    if (exchange.messageOk == true) AionColors.Green else AionColors.Amber,
                )
            }
            Spacer(Modifier.height(8.dp))
            Text(
                "建议使用只开「合约交易」、关闭「提现」权限的 API Key。",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
            )
        }

        AionCard {
            SectionHeader(title = "运行模式", subtitle = "来自 /system/status")
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "交易模式",
                    style = MaterialTheme.typography.bodySmall,
                    color = AionColors.Muted,
                    modifier = Modifier.weight(1f),
                )
                ModePill(system?.mode)
                if (system?.mode == null) {
                    Text("未知", style = MaterialTheme.typography.bodySmall, color = AionColors.Amber)
                }
            }
            Spacer(Modifier.height(6.dp))
            KeyValueRow("主网通道", if (system?.mainnetOn == true) "已开启" else "未开启", mono = false)
            if (system?.mode?.uppercase(Locale.US) != "MAINNET") {
                Spacer(Modifier.height(8.dp))
                InfoBanner(
                    "非主网模式：PAPER / TESTNET 标记会持续显示在顶部栏，避免误以为在真实市场下单。",
                    AionColors.Amber,
                )
            }
        }

        AionCard {
            SectionHeader(title = "AI 引擎", subtitle = "六个 AI 已连接到你的账户")
            Spacer(Modifier.height(8.dp))
            if (assignment?.configured == true) {
                KeyValueRow("引擎", assignment?.providerName ?: "AION", mono = false)
                KeyValueRow("状态", "已就绪", valueColor = AionColors.Green)
                KeyValueRow("工作方式", "自动选择模型", mono = false)
            } else {
                InfoBanner(
                    "AI 引擎正在初始化，请稍后下拉刷新。",
                    AionColors.Amber,
                    icon = Icons.Outlined.Info,
                )
            }
        }

        AionCard {
            SectionHeader(title = "安全", subtitle = "本机数据与凭据")
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.Top) {
                androidx.compose.material3.Icon(
                    Icons.Outlined.Lock,
                    contentDescription = null,
                    tint = AionColors.Muted,
                    modifier = Modifier
                        .padding(top = 2.dp)
                        .height(15.dp),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    "令牌使用 EncryptedSharedPreferences（AES-256）保存；密码不落盘；" +
                        "交易所密钥与模型密钥仅保存在 AION 云端，手机内不含任何密钥。",
                    style = MaterialTheme.typography.bodySmall,
                    color = AionColors.Muted,
                )
            }
            Spacer(Modifier.height(10.dp))
            HorizontalDivider(color = AionColors.Border.copy(alpha = 0.6f))
            Spacer(Modifier.height(6.dp))
            KeyValueRow("客户端版本", "0.1.0 (debug)", mono = false)
            KeyValueRow("数据目录", "EncryptedSharedPreferences/aion_session")
        }

        Button(
            onClick = { showLogout = true },
            enabled = !loggingOut,
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = AionColors.Red.copy(alpha = 0.14f),
                contentColor = AionColors.Red,
            ),
            shape = RoundedCornerShape(12.dp),
        ) {
            Text(if (loggingOut) "正在退出…" else "退出登录")
        }

        Text(
            "AION · 所有数据由云端实时同步。",
            style = MaterialTheme.typography.labelSmall,
            color = AionColors.Muted,
            modifier = Modifier.padding(bottom = 12.dp),
        )
    }
}

@Composable
private fun ModeButton(text: String, selected: Boolean, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        colors = ButtonDefaults.buttonColors(
            containerColor = if (selected) AionColors.Primary else AionColors.CardHigh,
            contentColor = if (selected) AionColors.Bg else AionColors.Text,
        ),
        shape = RoundedCornerShape(10.dp),
    ) {
        Text(text, fontFamily = FontFamily.Monospace)
    }
}
