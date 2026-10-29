# 管理入口拒绝诊断

`maivita.host.management` 沿用标准库 `logging.getLogger(__name__)`，拒绝时记录一条 WARNING。
子原因码只写服务端日志，不返回给群聊；`local_request()` 仍抛出
`ValueError('UNTRUSTED_CONTEXT')`，原有校验条件、检查顺序及调用方错误处理保持不变。
不记录消息正文、完整账号、群号、stream_id、任何密钥或异常详情。

日志的 `LogRecord` 通过 `extra` 携带以下结构化字段，同时消息为同一字段集的 JSON，
因此默认文本日志也能检索和解析，无需更改 Host 日志配置：

```text
WARNING {"matches": false, "present": true, "reason_code": "CONTEXT_MESSAGE_ID_INVALID", "stage": "message_id", "type_ok": true}
```

- `reason_code`：下表中的稳定子原因码。
- `stage`：发生失败的检查阶段；不含输入值。
- `present`：被检查字段的键是否存在；显式 null 也算存在。作为独立参数传入的 bot_account 用非 None 表示存在；user_info 父结构无效时为 false。
- `type_ok`：被检查字段的类型是否符合原校验预期；group_info 同时检查容器及群号类型。stream_id 沿用原校验的 isinstance，其余字符串沿用精确类型检查。
- `matches`：是否与期望匹配。格式、长度、结构、允许列表或标志校验失败时为 false；text 为与 processed_plain_text 的实际相等结果，所以仅超长但文本相同时可为 true。

| reason_code | stage | 失败含义 |
|---|---|---|
| CONTEXT_MESSAGE_NOT_DICT | message | message 不是 dict |
| CONTEXT_BOT_ACCOUNT_INVALID | bot_account | bot 账号类型或格式错误 |
| CONTEXT_USER_INFO_MISSING | user_info | message_info 或 user_info 缺失或结构错误 |
| CONTEXT_USER_ACCOUNT_INVALID | user_account | 作者账号类型、格式错误或等于 bot 账号 |
| CONTEXT_GROUP_INFO_INVALID | group_info | 群结构或群号类型、格式错误 |
| CONTEXT_TEXT_MISMATCH | text | text 类型、长度错误或与 processed_plain_text 不同 |
| CONTEXT_PLATFORM_MISMATCH | raw_platform / command_platform | 对应 platform 不等于 qq |
| CONTEXT_ACCOUNT_MISMATCH | account_id | 原始账号与 bot_account 不同 |
| CONTEXT_AUTHOR_MISMATCH | author | command.user_id 与原始作者不同 |
| CONTEXT_GROUP_MISMATCH | group_id | command.group_id 与原始群不同 |
| CONTEXT_SESSION_MISMATCH | session_id | 原始 session_id 与 stream_id 不同 |
| CONTEXT_STREAM_NOT_ALLOWED | stream_id | stream_allowed 返回 false |
| CONTEXT_MESSAGE_ID_INVALID | message_id | 消息 ID 类型或长度错误 |
| CONTEXT_SCOPE_INVALID | scope | 非 None 的 scope 类型或长度错误 |
| CONTEXT_FLAG_SET | is_bot / is_system / is_mai_message / is_notify | 对应标志存在且不严格为 False |
| MANAGEMENT_PARSE_FAILED | parse | management_requests.dispatch 的解析调用失败 |

每次仅记录按原检查顺序遇到的第一个失败；四个标志仍按表中顺序检查。
`management_requests.dispatch()` 直接复用 local_request 的诊断。
解析错误保留原有异常及群聊用法提示，不改成 UNTRUSTED_CONTEXT；解析诊断不记录异常文案。
本次未增加权限、策略、账本或执行阶段的诊断，未更改现有业务逻辑。
