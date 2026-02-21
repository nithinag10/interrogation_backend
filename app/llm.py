from langchain_openai import ChatOpenAI


class OpenAIClient:
    def __init__(self, model="gpt-4o", temperature=0.8):
        self.client = ChatOpenAI(model=model, temperature=temperature)

    def get_client(self):
        return self.client
