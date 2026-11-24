package dev.aion.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Dns
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material.icons.outlined.QueryStats
import androidx.compose.material.icons.outlined.ReceiptLong
import androidx.compose.material.icons.outlined.Science
import androidx.compose.material.icons.outlined.School
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material.icons.outlined.SmartToy
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import dev.aion.app.AppContainer
import dev.aion.app.ui.SessionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.ConfirmDialog
import dev.aion.app.ui.components.ModePill
import dev.aion.app.ui.components.Num
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.theme.AionColors

@Composable
fun MoreScreen(
    container: AppContainer,
    sessionVm: SessionViewModel,
    onNavigate: (String) -> Unit,
) {
    val me by sessionVm.me.collectAsState()
    val system by sessionVm.system.collectAsState()
    val loggingOut by sessionVm.loggingOut.collectAsState()
    var showLogout by remember { mutableStateOf(false) }

    if (showLogout) {
        ConfirmDialog(
            title = "退出登录",
            message = "将清除本机加密存储中的 access/refresh token，并断开 WebSocket。",
            confirmLabel = "退出登录",
            destructive = true,
            onDismiss = { showLogout = false },
            onConfirm = {
                showLogout = false
                sessionVm.logout()
            },
        )
    }

    androidx.compose.foundation.lazy.LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(20.dp))
                    .background(
                        Brush.linearGradient(
                            listOf(AionColors.Primary.copy(alpha = 0.22f), AionColors.Card),
                        ),
                    )
                    .padding(18.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier
                        .size(52.dp)
                        .clip(CircleShape)
                        .background(AionColors.Primary.copy(alpha = 0.22f)),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        (me?.username?.firstOrNull()?.uppercase() ?: "A"),
                        style = MaterialTheme.typography.titleLarge.copy(
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                        ),
                        color = AionColors.Primary,
                    )
                }
                Spacer(Modifier.width(14.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        me?.username ?: "已登录",
                        style = MaterialTheme.typography.titleLarge,
                        maxLines = 1,
                    )
                    Spacer(Modifier.height(4.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Pill(if (me?.isAdmin == true) "ADMIN" else "USER", if (me?.isAdmin == true) AionColors.Primary else AionColors.Muted)
                        if (me?.totpEnabled == true) {
                            Pill("2FA", AionColors.Green)
                        }
                        ModePill(system?.mode)
                    }
                }
            }
        }

        item {
            AionCard {
                SectionHeader(title = "更多功能", subtitle = "二级页面")
                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    NavTile(
                        icon = Icons.Outlined.ReceiptLong,
                        title = "订单与成交",
                        subtitle = "委托 / 历史 / 成交",
                        modifier = Modifier.weight(1f),
                        onClick = { onNavigate("orders") },
                    )
                    NavTile(
                        icon = Icons.Outlined.SmartToy,
                        title = "AI Brain",
                        subtitle = "Agent 运行记录",
                        modifier = Modifier.weight(1f),
                        onClick = { onNavigate("ai") },
                    )
                }
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    NavTile(
                        icon = Icons.Outlined.Shield,
                        title = "风控",
                        subtitle = "限值 / 事件 / 熔断",
                        modifier = Modifier.weight(1f),
                        onClick = { onNavigate("risk") },
                    )
                    NavTile(
                        icon = Icons.Outlined.QueryStats,
                        title = "策略",
                        subtitle = "策略状态一览",
                        modifier = Modifier.weight(1f),
                        onClick = { onNavigate("strategies") },
                    )
                }
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    NavTile(
                        icon = Icons.Outlined.PlayArrow,
                        title = "自动代理",
                        subtitle = "AI 全自动交易",
                        modifier = Modifier.weight(1f),
                        onClick = { onNavigate("autopilot") },
                    )
                    NavTile(
                        icon = Icons.Outlined.School,
                        title = "学习与进化",
                        subtitle = "经验与绩效",
                        modifier = Modifier.weight(1f),
                        onClick = { onNavigate("learning") },
                    )
                }
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    NavTile(
                        icon = Icons.Outlined.Science,
                        title = "因子研究",
                        subtitle = "候选因子与回测",
                        modifier = Modifier.weight(1f),
                        onClick = { onNavigate("factors") },
                    )
                    NavTile(
                        icon = Icons.Outlined.Dns,
                        title = "系统状态",
                        subtitle = "DB / Redis / 交易所",
                        modifier = Modifier.weight(1f),
                        onClick = { onNavigate("system") },
                    )
                    NavTile(
                        icon = Icons.Outlined.Settings,
                        title = "设置",
                        subtitle = "连接 / 账户",
                        modifier = Modifier.weight(1f),
                        onClick = { onNavigate("settings") },
                    )
                }
            }
        }

        item {
            AionCard {
                SectionHeader(title = "连接信息")
                Spacer(Modifier.height(8.dp))
                Num(
                    "AION 云服务",
                    style = MaterialTheme.typography.bodyMedium,
                    color = AionColors.Muted,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "实时通道：${if (system != null) "已登录" else "未连接"} · ${container.session.username ?: ""}",
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                )
            }
        }

        item {
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
        }

        item {
            Text(
                "AION · 个人加密货币合约交易系统 · Android 客户端",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
                modifier = Modifier.padding(bottom = 12.dp),
            )
        }
    }
}

@Composable
private fun NavTile(
    icon: ImageVector,
    title: String,
    subtitle: String,
    modifier: Modifier,
    onClick: () -> Unit,
) {
    AionCard(
        modifier = modifier,
        onClick = onClick,
        padding = PaddingValues(14.dp),
    ) {
        Box(
            modifier = Modifier
                .size(34.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(AionColors.Primary.copy(alpha = 0.14f)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, contentDescription = null, tint = AionColors.Primary, modifier = Modifier.size(18.dp))
        }
        Spacer(Modifier.height(10.dp))
        Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 1)
        Spacer(Modifier.height(2.dp))
        Text(
            subtitle,
            style = MaterialTheme.typography.labelSmall,
            color = AionColors.Muted,
            maxLines = 1,
        )
    }
}
