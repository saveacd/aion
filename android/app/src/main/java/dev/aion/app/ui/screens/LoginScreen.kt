package dev.aion.app.ui.screens

import androidx.compose.foundation.Image
import androidx.compose.ui.res.painterResource
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.aion.app.AppContainer
import dev.aion.app.ui.SessionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.AionField
import dev.aion.app.ui.components.InfoBanner
import dev.aion.app.ui.components.Num
import dev.aion.app.ui.theme.AionColors

@Composable
fun LoginScreen(container: AppContainer, sessionVm: SessionViewModel) {
    val loginState by sessionVm.loginState.collectAsState()
    var username by rememberSaveable { mutableStateOf(container.session.username.orEmpty()) }
    var password by rememberSaveable { mutableStateOf("") }
    var totp by rememberSaveable { mutableStateOf("") }
    var registerMode by rememberSaveable { mutableStateOf(false) }

    fun submit() {
        if (registerMode) sessionVm.register(username, password) else sessionVm.login(username, password, totp)
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 48.dp),
            verticalArrangement = Arrangement.Center,
        ) {
            Image(
                painter = painterResource(id = dev.aion.app.R.drawable.aion_brand),
                contentDescription = "AION 官方标识",
                modifier = Modifier
                    .size(104.dp)
                    .clip(RoundedCornerShape(22.dp)),
            )
            Spacer(Modifier.height(20.dp))
            Text("AION", style = MaterialTheme.typography.headlineSmall.copy(letterSpacing = 4.sp))
            Spacer(Modifier.height(6.dp))
            Text(
                "Autonomous Intelligence & Operations Network",
                style = MaterialTheme.typography.bodyMedium,
                color = AionColors.Muted,
            )

            Spacer(Modifier.height(28.dp))

            AionCard {
                Text(
                    "AION 安全云服务",
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                    fontFamily = FontFamily.Monospace,
                )
                Spacer(Modifier.height(12.dp))
                AionField(
                    value = username,
                    onValueChange = { username = it },
                    label = "用户名",
                    enabled = !loginState.submitting,
                )
                Spacer(Modifier.height(12.dp))
                AionField(
                    value = password,
                    onValueChange = { password = it },
                    label = "密码",
                    keyboardType = KeyboardType.Password,
                    enabled = !loginState.submitting,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardActions = KeyboardActions(onDone = { submit() }),
                )
                if (!registerMode) {
                    Spacer(Modifier.height(12.dp))
                    AionField(
                        value = totp,
                        onValueChange = { totp = it.filter { ch -> ch.isDigit() }.take(8) },
                        label = "TOTP 验证码（可选）",
                        placeholder = "已启用 2FA 时填写",
                        keyboardType = KeyboardType.NumberPassword,
                        mono = true,
                        enabled = !loginState.submitting,
                        keyboardActions = KeyboardActions(onDone = { submit() }),
                    )
                }

                Spacer(Modifier.height(20.dp))

                Button(
                    onClick = { submit() },
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(50.dp),
                    enabled = !loginState.submitting,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = AionColors.Primary,
                        disabledContainerColor = AionColors.Primary.copy(alpha = 0.45f),
                    ),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    if (loginState.submitting) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = AionColors.Text,
                        )
                        Spacer(Modifier.width(10.dp))
                        Text(if (registerMode) "正在注册…" else "正在登录…")
                    } else {
                        Text(
                            if (registerMode) "注册并开通模拟盘" else "登录",
                            style = MaterialTheme.typography.titleMedium,
                        )
                    }
                }

                Spacer(Modifier.height(12.dp))
                Text(
                    if (registerMode) "已有账号？返回登录" else "还没有账号？免费注册",
                    style = MaterialTheme.typography.bodySmall,
                    color = AionColors.Primary,
                    textAlign = TextAlign.Center,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable(enabled = !loginState.submitting) {
                            registerMode = !registerMode
                            sessionVm.clearLoginError()
                        },
                )

                if (loginState.error != null) {
                    Spacer(Modifier.height(14.dp))
                    InfoBanner(text = loginState.error ?: "", color = AionColors.Red)
                    if (loginState.requestId != null) {
                        Spacer(Modifier.height(6.dp))
                        Num(
                            "request_id: ${loginState.requestId}",
                            style = MaterialTheme.typography.labelSmall,
                            color = AionColors.Muted,
                        )
                    }
                }
            }

            Spacer(Modifier.height(18.dp))

            Row(verticalAlignment = Alignment.Top) {
                Icon(
                    Icons.Outlined.Lock,
                    contentDescription = null,
                    tint = AionColors.Muted,
                    modifier = Modifier
                        .padding(top = 2.dp)
                        .size(15.dp),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    "登录凭据通过加密通道发送；会话令牌保存在本机加密存储中。" +
                        "交易所密钥与模型密钥不会保存在手机中。",
                    style = MaterialTheme.typography.bodySmall,
                    color = AionColors.Muted,
                )
            }

            Spacer(Modifier.height(8.dp))
            Text(
                "AION · 个人交易终端",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted.copy(alpha = 0.7f),
                modifier = Modifier.fillMaxWidth(),
                textAlign = TextAlign.Center,
            )
        }
    }
}
