class FakeLLM:
    def __init__(self, reply: str = "这是一个测试回答。"):
        self.reply = reply

    async def stream_chat(self, messages: list[dict], **params):
        yield self.reply

    async def complete(self, messages: list[dict], timeout: float | None = None, **params) -> str:
        """非流式一次性生成（与 OpenAICompatLLM.complete 对齐，供测试复用）。"""
        return self.reply
