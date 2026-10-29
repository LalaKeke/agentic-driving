# 正式 OpenAI API 接入验证

已完成实际验证：官方 `/v1/models` 返回HTTP200并列出 `gpt-6.1-sol`；随后一条纯文本及一条10张真实图片的 Structured Outputs 请求均返回HTTP200和合法原生JSON。两条生成诊断分别耗时约4.29秒、4.84秒，无自动重试。这仅证明本次接入可用，尚不构成长期稳定性比较或驾驶成功。

使用 `https://api.openai.com/v1/responses`，保留 `gpt-6.1-sol`；服务 `response.model` 同名，独立快照仍未知。用户指定的私有凭据文件只在内存读取，文件权限已收紧为0600，密钥和Authorization头未提交或输出。代理继续使用现成17891端口，不配置转发；TLS证书校验保持。

新增适配器只改变传输。业务契约3.0.0-exp2、wire v2、提示词及T1–T4保持；原生文本先落盘，再按原业务Schema校验，无补字段、强转或预测填充。单次decide只发一次POST，服务/认证/配额错误停止。API调用与旧Codex RPC分别计数，保留请求ID、服务模型和usage。

源码提交 `9b177ee`；完整离线回归416项通过，59.852秒、真实调用0、Python网络连接禁止。旧订阅通道结果保留，不继承其剩余预算。

本轮诊断生成2次，模型列表GET不计生成。驾驶六时刻及三组完整失败历史另列于[最终场景结果](../api_continuous_bend_20261029_v2/README.md)，结果以实际台账为准。

官方接口依据：[Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)、[图像输入](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)。当前两次诊断不能证明整个驾驶业务Schema已被服务接受；该事实由随后驾驶批次单独记录。
