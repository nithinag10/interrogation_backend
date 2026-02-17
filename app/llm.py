from langchain_openai import ChatOpenAI


class OpenAIClient:
    def __init__(self):
        self.client = ChatOpenAI(model="gpt-4o", temperature=0.8)

    def get_client(self):
        return self.client
