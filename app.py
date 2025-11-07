from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
import json
import asyncio
import httpx
import os
from typing import List, Dict

app = FastAPI()

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")

# 配置OpenAI客户端连接到MiniMax
api_key = os.getenv("MINIMAX_API_KEY")
client = OpenAI(
    base_url="https://api.minimaxi.com/v1",
    api_key=api_key
)

# 定义工具：天气查询
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取指定城市的实时天气信息，包括温度、天气状况、湿度等。用户需要先提供一个城市名称。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "城市名称，例如：北京、上海、San Francisco 等"
                    }
                },
                "required": ["location"]
            }
        }
    }
]


async def get_weather(location: str) -> str:
    """
    调用wttr.in免费天气API获取实时天气
    参数：
        location: 城市名称
    返回：
        天气信息的JSON字符串
    """
    try:
        async with httpx.AsyncClient() as client_http:
            url = f"https://wttr.in/{location}?format=j1"
            response = await client_http.get(url, timeout=10.0)
            
            if response.status_code == 200:
                data = response.json()
                current = data['current_condition'][0]
                
                weather_info = {
                    "location": location,
                    "temperature": f"{current['temp_C']}°C",
                    "feels_like": f"{current['FeelsLikeC']}°C",
                    "condition": current['weatherDesc'][0]['value'],
                    "humidity": f"{current['humidity']}%",
                    "wind_speed": f"{current['windspeedKmph']} km/h",
                    "wind_direction": current['winddir16Point'],
                    "pressure": f"{current['pressure']} mb",
                    "visibility": f"{current['visibility']} km",
                    "uv_index": current['uvIndex']
                }
                
                return json.dumps(weather_info, ensure_ascii=False)
            else:
                return json.dumps({"error": f"无法获取{location}的天气信息"}, ensure_ascii=False)
                
    except Exception as e:
        return json.dumps({"error": f"天气查询失败: {str(e)}"}, ensure_ascii=False)


@app.get("/")
async def read_root():
    """重定向到静态页面"""
    from fastapi.responses import FileResponse
    return FileResponse("static/index.html")


@app.post("/api/chat/stream")
async def chat_stream(request: Request):
    """流式聊天接口（支持工具调用）"""
    try:
        # 解析请求体
        body = await request.json()
        messages = body.get("messages", [])
        
        if not messages:
            return {"error": "messages字段不能为空"}
        
        # 创建流式生成器
        async def generate():
            try:
                # 第一轮：调用模型（使用流式以实时返回思考过程）
                stream = client.chat.completions.create(
                    model="MiniMax-M2",
                    messages=messages,
                    tools=tools,
                    extra_body={"reasoning_split": True},
                    stream=True,
                )
                
                # 收集完整的响应消息（用于检测tool_calls）
                full_content = ""
                tool_calls_list = []
                reasoning_content = []
                
                # 处理第一轮流式响应
                for chunk in stream:
                    try:
                        delta = chunk.choices[0].delta
                    except Exception:
                        continue
                    
                    # 实时发送思考过程
                    rd = getattr(delta, "reasoning_details", None)
                    if rd:
                        for detail in rd:
                            if isinstance(detail, dict) and "text" in detail and detail["text"]:
                                reasoning_content.append(detail["text"])
                                data = json.dumps({"type": "thinking", "content": detail["text"]}, ensure_ascii=False)
                                yield f"data: {data}\n\n"
                                await asyncio.sleep(0.01)
                    
                    # 收集内容（暂不发送，等确认是否有工具调用）
                    content_fragment = getattr(delta, "content", None)
                    if content_fragment:
                        full_content += content_fragment
                    
                    # 收集工具调用信息
                    tool_calls = getattr(delta, "tool_calls", None)
                    if tool_calls:
                        for tc in tool_calls:
                            # 查找或创建对应的tool_call
                            idx = tc.index if hasattr(tc, "index") else 0
                            while len(tool_calls_list) <= idx:
                                tool_calls_list.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            
                            if hasattr(tc, "id") and tc.id:
                                tool_calls_list[idx]["id"] = tc.id
                            if hasattr(tc, "function"):
                                if hasattr(tc.function, "name") and tc.function.name:
                                    tool_calls_list[idx]["function"]["name"] = tc.function.name
                                if hasattr(tc.function, "arguments") and tc.function.arguments:
                                    tool_calls_list[idx]["function"]["arguments"] += tc.function.arguments
                
                # 检查是否有工具调用
                if tool_calls_list:
                    # 发送工具调用信息
                    for tool_call in tool_calls_list:
                        tool_info = f"🔧 正在调用工具: {tool_call['function']['name']}\n"
                        data = json.dumps({"type": "content", "content": tool_info}, ensure_ascii=False)
                        yield f"data: {data}\n\n"
                        await asyncio.sleep(0.01)
                    
                    # 将完整的assistant响应添加到消息历史
                    messages.append({
                        "role": "assistant",
                        "content": full_content or None,
                        "tool_calls": tool_calls_list
                    })
                    
                    # 执行工具调用
                    for tool_call in tool_calls_list:
                        function_name = tool_call['function']['name']
                        function_args = json.loads(tool_call['function']['arguments'])
                        
                        if function_name == "get_weather":
                            location = function_args.get("location")
                            tool_result = await get_weather(location)
                            
                            # 发送工具执行结果信息
                            result_info = f"📊 获取到{location}的天气信息\n"
                            data = json.dumps({"type": "content", "content": result_info}, ensure_ascii=False)
                            yield f"data: {data}\n\n"
                            await asyncio.sleep(0.01)
                            
                            # 添加工具结果到消息历史
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call['id'],
                                "content": tool_result
                            })
                    
                    # 第二轮：使用流式获取最终回复
                    stream = client.chat.completions.create(
                        model="MiniMax-M2",
                        messages=messages,
                        tools=tools,
                        extra_body={"reasoning_split": True},
                        stream=True,
                    )
                    
                    # 处理流式响应
                    for chunk in stream:
                        try:
                            delta = chunk.choices[0].delta
                        except Exception:
                            continue
                        
                        # 处理思考过程
                        rd = getattr(delta, "reasoning_details", None)
                        if rd:
                            for detail in rd:
                                if isinstance(detail, dict) and "text" in detail and detail["text"]:
                                    data = json.dumps({"type": "thinking", "content": detail["text"]}, ensure_ascii=False)
                                    yield f"data: {data}\n\n"
                                    await asyncio.sleep(0.01)
                        
                        # 处理响应内容
                        content_fragment = getattr(delta, "content", None)
                        if content_fragment:
                            data = json.dumps({"type": "content", "content": content_fragment}, ensure_ascii=False)
                            yield f"data: {data}\n\n"
                            await asyncio.sleep(0.01)
                
                else:
                    # 没有工具调用，流式发送已收集的内容
                    if full_content:
                        # 直接实时发送（在流式处理中其实已经可以发送了，这里分块发送）
                        chunk_size = 10
                        for i in range(0, len(full_content), chunk_size):
                            chunk_text = full_content[i:i+chunk_size]
                            data = json.dumps({"type": "content", "content": chunk_text}, ensure_ascii=False)
                            yield f"data: {data}\n\n"
                            await asyncio.sleep(0.01)
                
                # 发送完成信号
                yield "data: [DONE]\n\n"
                
            except Exception as e:
                error_msg = f"错误: {str(e)}"
                yield f"data: {error_msg}\n\n"
        
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
        
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/health")
async def health_check():
    """健康检查接口"""
    return {"status": "ok", "message": "MiniMax Chat服务运行中"}


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("MiniMax Chat Web服务启动中...")
    print("访问地址: http://localhost:8000")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000)

