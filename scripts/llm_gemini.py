
from langchain_google_genai import ChatGoogleGenerativeAI

MODEL_NAME = "gemini-3.5-flash-lite"

def make_llm(temperature: float = 0.0):
    return ChatGoogleGenerativeAI(
        model=MODEL_NAME,
        google_api_key=os.environ["GOOGLE_API_KEY"],
        temperature=temperature,
        max_retries=3,
    )
