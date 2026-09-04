class FakeLLM:
    def __init__(self, reply: str = "这是一个测试回答。"):
        self.reply = reply

    async def stream_chat(self, messages: list[dict], **params):
        yield self.reply
