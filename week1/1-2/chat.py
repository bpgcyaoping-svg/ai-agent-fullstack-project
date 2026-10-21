import os
from openai import OpenAI


client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
    timeout=30.0,
    max_retries=0,
)

messages = [
    {
        "role": "system", 
        "content": "你是代码审查助手。只指出会影响正确性的问题，没有证据时不要猜测。"
    },
    {
        "role": "user",
        "content": "请审查以下代码：def divide(a, b): return a / b \n",
    }
]

completion = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    max_tokens=512,
    extra_body={"thinking": {"type": "disabled"}},
)

choice = completion.choices[0]
print(choice.message.content)
print("finish_reason:", choice.finish_reason)
print("usage:", completion.usage)
