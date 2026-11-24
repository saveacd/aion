package dev.aion.app.data

sealed interface ApiResult<out T> {

    data class Success<T>(val data: T) : ApiResult<T>

    data class Failure(
        val httpStatus: Int? = null,
        val code: String? = null,
        val message: String,
        val requestId: String? = null,
        val details: String? = null,
    ) : ApiResult<Nothing> {

        /** Human readable text. Keeps the server message verbatim. */
        fun userMessage(): String {
            val server = message.trim()
            return when {
                code == "NETWORK" -> server
                httpStatus == 401 -> "登录已失效，请重新登录。"
                httpStatus == 403 -> server.ifBlank { "当前账户没有权限执行该操作。" }
                httpStatus == 404 -> if (server.isBlank()) "接口不存在（404）。" else "接口不存在（404）：$server"
                httpStatus == 501 || httpStatus == 503 ->
                    if (server.isBlank()) "服务暂时不可用，请稍后重试（HTTP $httpStatus）。" else server
                httpStatus != null && server.isBlank() -> "请求失败（HTTP $httpStatus）。"
                server.isBlank() -> "请求失败。"
                else -> server
            }
        }
    }
}

data class Loadable<T>(
    val data: T? = null,
    val loading: Boolean = false,
    val error: String? = null,
    val requestId: String? = null,
)
