# 基础练习
# 异步制作茶
# ===== gather()：等所有完成，返回列表（保持顺序） =====
import time
import asyncio

# from python.exception.exception import response
# async def boil_water():
#     print("开始烧水")
#     await asyncio.sleep(3)
#     print("水烧开了")

# async def wash_cup():
#     print("开始洗杯")
#     await asyncio.sleep(0.2)
#     print("杯洗完了")

# async def add_tea():
#     print("开始加茶")
#     await asyncio.sleep(0.5)
#     print("茶加完了")
    
# async def main():
#     print("开始异步制作茶")
#     start= time.time()
#     await asyncio.gather(
#         boil_water(), 
#         wash_cup(), 
#         add_tea()
#     )
#     end = time.time()
#     print(f"异步制作茶总耗时: {end-start}")
#     print("异步制作茶完成")

# asyncio.run(main())

# 用 asyncio.create_task() 实现"发出但不等"
import asyncio
async def download_file(filename: str, duration: float):
    print(f"开始下载 {filename}")
    await asyncio.sleep(duration)  # 模拟下载时间
    print(f"{filename} 下载完成")



async def main_create_task():
    start = time.time()

    task_a = asyncio.create_task(download_file("file_a.txt", 2))
    task_b = asyncio.create_task(download_file("file_b.txt", 3))
    task_c = asyncio.create_task(download_file("file_c.txt", 4))

    print("三个下载任务已启动，可以干别的事情")
    await asyncio.sleep(0.5)
    print("所有下载任务完成")

    content_a = await task_a
    content_b = await task_b
    content_c = await task_c
    print(f"全部下载完成，总耗时：{time.time()-start}")

asyncio.run(main_create_task())    

# ===== as_completed()：哪个先完成就先处理哪个 =====
async def fetch(url:str, delay:float)->str:
    await asyncio.sleep(delay)
    return f"来自 {url} 的内容：{url}"


async def main_as_completed():
    start = time.time()

    tasks= [
        fetch("url_a", 3),
        fetch("url_b", 1),
        fetch("url_c", 2),
    ]
    

    for done in asyncio.as_completed(tasks):
        content = await done
        print(f"收到结果 {content}")

asyncio.run(main_as_completed())    


# 实战 1：并发调用多个 LLM 模型，取最快结果
import httpx
import asyncio
async def call_model(
    client: httpx.AsyncClient,
    model_name: str,
    api_key: str,
    prompt: str,
) -> dict:
    """
    调用 LLM 模型
    """
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }
    start = time.time()
    response = await client.post(url, headers=headers, json=payload, timeout=10)
    elapsed = time.time() -start
    print(f"调用模型 {model_name} 耗时: {elapsed}")
    if response.status_code == 200:
        data = response.json()
        return {
            "model_name": model_name,
            "elapsed": elapsed,
            "content": data["choices"][0]["message"]["content"],
        }
    else:
        return  {
            "model_name": model_name,
            "elapsed": elapsed,
            "error_status": response.status_code,
        }  


async def call_multi_model_race(
    prompt: str, 
    api_key: str,
    ) -> dict:
    """
    同时调用多个模型，谁先完成就用谁的结果。
    场景：对响应速度要求高，不挑模型，要最快的结果。    
    """
    models = [
        "gpt-3.5-turbo",
        "gpt-4o",
        "gpt-4o-mini",
    ]
    async with httpx.AsyncClient() as client:
        tasks = [
            call_model(client, model_name, api_key, prompt)
            for model_name in models
        ]
        # results = await asyncio.as_completed(tasks)
        # for result in results:
        #     print(result)
        # return results
        for coro in  asyncio.as_completed(tasks):
            result = await coro
            if "error" not in result:
                print(f"最快模型 {result['model_name']} 耗时: {result['elapsed']}")
                # 取消其他还在跑的请求(可选)
                for task in tasks:
                    task.cancel()
            else:
                print(f"模型 {result['model_name']} 调用失败，状态码: {result['error_status']}")
        return {"error": "所有模型都调用失败"}
# 运行
result = asyncio.run(call_multi_model_race("你好", "sk-1234567890abcdef1234567890abcdef"))
print(result)

# 实战 2：带超时 + 重试的异步 API 调用
async def call_api_with_retry(
    client: httpx.AsyncClient,
    url: str,
    timeout: float = 10,
    max_retries: int = 3,
) -> dict:
    """
    异步 API 调用：超时保护 + 指数退避重试。
    """
    for attempt in range(max_retries):
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"请求失败，状态码: {response.status_code}")
                await asyncio.sleep(retry_delay)
        except asyncio.TimeoutError:
            if attempt == max_retries - 1:
                raise RuntimeError(f"请求 {url} 超时，超过最大重试次数 {max_retries} 次")
            wait_time = 2 ** attempt
            print(f"请求 {url} 超时，等待 {wait_time} 秒后重试")
            await asyncio.sleep(wait_time)

        except httpx.HTTPStatusError as e:
            if e.response. status_code == 429:
                retry_after = int(e.response.headers.get("Retry-After", 5))
                print(f"请求 {url} 趫限，等待 {retry_after} 秒后重试")
                await asyncio.sleep(retry_after)
            elif e.response.status_code >= 500:
                wait_time = 2 ** attempt
                print(f"请求 {url} 服务器错误，等待 {wait_time} 秒后重试")
                await asyncio.sleep(wait_time)
            else:
                raise  # 4xx 不重试
    return {"error": "请求失败，超过最大重试次数"}
async def main_call_api_with_retry():
    async with httpx.AsyncClient() as client:
        result = await call_api_with_retry(
            client, 
            "https://httpbin.org/json", 
        )
        print(result)
print(asyncio.run(main_call_api_with_retry()))

# 实战 3：并发搜索——同时查多个数据源，合并结果
import httpx
import asyncio

async def search_source(
    client: httpx.AsyncClient,
    name: str,
    query: str,
) -> dict:
    """模拟搜索一个数据源"""
    await asyncio.sleep(1)       # 模拟网络延迟
    return {
        "source": name,
        "results": [f"{name} 中关于 '{query}' 的结果 1", f"结果 2"],
    }

async def concurrent_search(query: str) -> list[dict]:
    """同时搜索多个数据源，合并所有结果"""
    sources = ["文档库", "代码库", "知识图谱", "网络搜索", "历史记录"]

    async with httpx.AsyncClient() as client:
        tasks = [
            search_source(client, source, query)
            for source in sources
        ]
        # gather 等待所有完成。也可以用 as_completed 先展示快的
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

    # 分离成功和失败
    success = []
    for result in all_results:
        if isinstance(result, Exception):
            print(f"[警告] 某个搜索源失败：{result}")
        else:
            success.append(result)

    print(f"成功搜索 {len(success)}/{len(sources)} 个数据源")
    return success

# 5 个源并发查询，总耗时 ≈ 1 秒（不是 5 秒）
# result = asyncio.run(concurrent_search("AI Agent 架构"))

# 实战 4：信号量（Semaphore）限制并发数,当你同时调用 1000 个 API 时，可能触发限流或者耗尽连接池。信号量可以限制同时进行的协程数量。
async def limited_api_call( 
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    item_id: int,
 ) -> dict:
    """受信号量限制的 API 调用"""
    async with sem:
        print(f"开始调用 API {item_id}")
        await asyncio.sleep(0.5)
        print(f"调用 API {item_id} 完成")
        return {"item_id": item_id, "status": "success"}

async def batch_with_limit(
    total: int = 20, max_concurrent: int = 5
) -> list[dict]:
    """
    批量请求，但最多同时进行 5 个。
    5 个完成一个，就放行下一个——始终保持不超过 5 个在跑。
    """
    sem = asyncio.Semaphore(max_concurrent)
    
    async with httpx.AsyncClient() as client:
        tasks = [
           limited_api_call(client, sem, item_id) for item_id in total
        ]
        results = await asyncio.gather(*tasks)
    print(f"全部 {total} 个 API 调用完成")    
    return results


async def main_batch_with_limit():
    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(5)
        item_ids = [i for i in range(1000)]
        results = await batch_with_limit(
            client, sem, item_ids
        )
        print(results)
print(asyncio.run(main_batch_with_limit()))


# AsyncClient 的核心写法速记
async def fetch_all(urls: list[str]) -> list[dict]:
    """并发请求多个 URL"""
    async with httpx.AsyncClient(timeout=5) as client:
        tasks = [
            client.get(url) for url in urls
        ]

        responses = await asyncio.gather(*tasks)
        results = []
        for resp in responses:
            if resp.status_code == 200:
                results.append(resp.json())
            else:
                results.append({"error": f"请求 {url} 失败，状态码 {resp.status_code}"})
    return results

print(asyncio.run(fetch_all(["https://httpbin.org/json", "https://httpbin.org/get"])))