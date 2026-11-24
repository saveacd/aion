package dev.aion.app.ui

import androidx.compose.foundation.Image
import androidx.compose.ui.res.painterResource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.AccountBalanceWallet
import androidx.compose.material.icons.outlined.Dashboard
import androidx.compose.material.icons.outlined.GridView
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.ShowChart
import androidx.compose.material.icons.outlined.SwapVert
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import dev.aion.app.AionApplication
import dev.aion.app.AppContainer
import dev.aion.app.ui.components.ModePill
import dev.aion.app.ui.components.StatusDot
import dev.aion.app.ui.screens.AiBrainScreen
import dev.aion.app.ui.screens.AutopilotScreen
import dev.aion.app.ui.screens.DashboardScreen
import dev.aion.app.ui.screens.FactorResearchScreen
import dev.aion.app.ui.screens.LearningScreen
import dev.aion.app.ui.screens.LoginScreen
import dev.aion.app.ui.screens.MarketScreen
import dev.aion.app.ui.screens.MoreScreen
import dev.aion.app.ui.screens.OrdersScreen
import dev.aion.app.ui.screens.PositionsScreen
import dev.aion.app.ui.screens.RiskScreen
import dev.aion.app.ui.screens.ResearchLabScreen
import dev.aion.app.ui.screens.SettingsScreen
import dev.aion.app.ui.screens.StrategiesScreen
import dev.aion.app.ui.screens.SystemScreen
import dev.aion.app.ui.screens.TradingScreen
import dev.aion.app.ui.theme.AionColors

private enum class Tab(
    val route: String,
    val label: String,
    val icon: ImageVector,
) {
    Dashboard("dashboard", "总览", Icons.Outlined.Dashboard),
    Market("market", "行情", Icons.Outlined.ShowChart),
    Trading("trading", "交易", Icons.Outlined.SwapVert),
    Positions("positions", "持仓", Icons.Outlined.AccountBalanceWallet),
    More("more", "更多", Icons.Outlined.GridView),
}

private val secondaryTitles = mapOf(
    "orders" to "订单与成交",
    "ai" to "AI Brain",
    "autopilot" to "自动代理",
    "learning" to "学习与进化",
    "risk" to "风控",
    "strategies" to "策略",
    "factors" to "因子研究",
    "research-lab" to "自主研究实验室",
    "system" to "系统状态",
    "settings" to "设置",
)

@Composable
fun AppRoot() {
    val context = LocalContext.current
    val container = remember {
        (context.applicationContext as AionApplication).container
    }
    val loggedIn by container.appState.loggedIn.collectAsState()
    val sessionVm: SessionViewModel = aionViewModel(container) { SessionViewModel(it) }

    LaunchedEffect(loggedIn) {
        if (loggedIn) {
            container.realtime.connect()
            sessionVm.bootstrap()
        } else {
            container.realtime.disconnect()
        }
    }

    if (loggedIn) {
        MainShell(container, sessionVm)
    } else {
        LoginScreen(container, sessionVm)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MainShell(container: AppContainer, sessionVm: SessionViewModel) {
    val nav = rememberNavController()
    val backStack by nav.currentBackStackEntryAsState()
    val route = backStack?.destination?.route ?: Tab.Dashboard.route
    val connection by container.appState.connection.collectAsState()
    val system by container.appState.system.collectAsState()
    val isTab = Tab.entries.any { it.route == route }
    val title = Tab.entries.firstOrNull { it.route == route }?.label
        ?: secondaryTitles[route]
        ?: "AION"

    Scaffold(
        containerColor = AionColors.Bg,
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Image(
                            painter = painterResource(id = dev.aion.app.R.drawable.aion_brand),
                            contentDescription = "AION",
                            modifier = Modifier
                                .size(30.dp)
                                .padding(end = 5.dp),
                        )
                        Text(title, style = MaterialTheme.typography.titleLarge)
                        if (system?.mode != null) {
                            Spacer(Modifier.width(10.dp))
                            ModePill(system?.mode)
                        }
                    }
                },
                navigationIcon = {
                    if (!isTab) {
                        IconButton(onClick = { nav.popBackStack() }) {
                            Icon(
                                Icons.AutoMirrored.Outlined.ArrowBack,
                                contentDescription = "返回",
                            )
                        }
                    }
                },
                actions = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                        modifier = Modifier.padding(end = 4.dp),
                    ) {
                        StatusDot(connection)
                        IconButton(onClick = { container.appState.requestRefresh() }) {
                            Icon(
                                Icons.Outlined.Refresh,
                                contentDescription = "刷新",
                                tint = AionColors.Muted,
                            )
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = AionColors.Bg,
                    titleContentColor = AionColors.Text,
                    navigationIconContentColor = AionColors.Text,
                    actionIconContentColor = AionColors.Muted,
                ),
            )
        },
        bottomBar = {
            if (isTab) {
                AionBottomBar(route) { target ->
                    if (target.route != route) {
                        nav.navigate(target.route) {
                            popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                            launchSingleTop = true
                            restoreState = true
                        }
                    }
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = nav,
            startDestination = Tab.Dashboard.route,
            modifier = Modifier.padding(padding),
        ) {
            composable("dashboard") { DashboardScreen(container) { nav.navigate(it) } }
            composable("market") { MarketScreen(container) }
            composable("trading") { TradingScreen(container) }
            composable("positions") { PositionsScreen(container) { nav.navigate(it) } }
            composable("more") { MoreScreen(container, sessionVm) { nav.navigate(it) } }
            composable("orders") { OrdersScreen(container) }
            composable("ai") { AiBrainScreen(container) }
            composable("autopilot") { AutopilotScreen(container) }
            composable("learning") { LearningScreen(container) }
            composable("risk") { RiskScreen(container) }
            composable("strategies") { StrategiesScreen(container) }
            composable("factors") { FactorResearchScreen(container) }
            composable("research-lab") { ResearchLabScreen(container) }
            composable("system") { SystemScreen(container) { nav.navigate(it) } }
            composable("settings") { SettingsScreen(container, sessionVm) }
        }
    }
}

@Composable
private fun AionBottomBar(route: String, onSelect: (Tab) -> Unit) {
    NavigationBar(
        containerColor = AionColors.Card,
        tonalElevation = 0.dp,
    ) {
        Tab.entries.forEach { tab ->
            val selected = route == tab.route
            NavigationBarItem(
                selected = selected,
                onClick = { onSelect(tab) },
                icon = { Icon(tab.icon, contentDescription = tab.label) },
                label = {
                    Text(
                        tab.label,
                        style = MaterialTheme.typography.labelSmall,
                    )
                },
                colors = NavigationBarItemDefaults.colors(
                    selectedIconColor = AionColors.Primary,
                    selectedTextColor = AionColors.Primary,
                    unselectedIconColor = AionColors.Muted,
                    unselectedTextColor = AionColors.Muted,
                    indicatorColor = AionColors.Primary.copy(alpha = 0.12f),
                ),
            )
        }
    }
}

@Composable
fun GlobalRefreshEffect(container: AppContainer, action: () -> Unit) {
    val tick by container.appState.refreshSignal.collectAsState()
    LaunchedEffect(tick) {
        if (tick > 0) action()
    }
}

@Composable
fun goBack(nav: NavHostController) = nav.popBackStack()
