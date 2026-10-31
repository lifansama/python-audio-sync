import os
import time
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

app = FastAPI()

# CSP 响应头中间件
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: ws: wss: https: http:; "
        "connect-src 'self' ws: wss: https: http:;"
    )
    return response

# 定义支持的年级频道列表（g1, g2, g3），并为每个年级初始化独立的目录与状态
GRADES = ["g1", "g2", "g3", "default"]
grade_states = {}

for grade in GRADES:
    audio_dir = Path(f"static/audio/{grade}")
    audio_dir.mkdir(parents=True, exist_ok=True)
    grade_states[grade] = {
        "audio_dir": audio_dir,
        "clients": set(),
        "playback_state": {
            "status": "stopped",       # "playing", "paused", "stopped"
            "filename": None,
            "target_time": 0,
            "start_pos": 0.0
        }
    }

def get_grade_from_host(host: str) -> str:
    """根据访问的域名（Host 头）自动识别年级，支持 g1, g2, g3"""
    if not host:
        return "default"
    subdomain = host.split(".")[0].lower()
    if subdomain in ["g1", "g2", "g3"]:
        return subdomain
    return "default"

def get_audio_files(grade: str):
    audio_dir = grade_states[grade]["audio_dir"]
    if not audio_dir.exists():
        audio_dir.mkdir(parents=True, exist_ok=True)
    
    mp3_files = []
    for file in os.listdir(audio_dir):
        if file.lower().endswith(".mp3"):
            mp3_files.append(file)
    mp3_files.sort()
    return mp3_files

async def broadcast_client_count(grade: str):
    """向对应年级的在线客户端广播当前的连接设备数"""
    state = grade_states[grade]
    count = len(state["clients"])
    message = {"type": "client_count", "count": count}
    for client in list(state["clients"]):
        try:
            await client.send_json(message)
        except:
            state["clients"].discard(client)

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

# 💡 关键点 1：必须把自定义音频路由写在 app.mount 之前，确保优先拦截处理多域名/多目录隔离
@app.get("/static/audio/{filename}")
async def get_audio_file(request: Request, filename: str):
    host = request.headers.get("host", "")
    grade = get_grade_from_host(host)
    file_path = grade_states[grade]["audio_dir"] / filename
    if file_path.exists():
        return FileResponse(file_path)
    return {"error": "File not found"}, 404

# 挂载通用静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.post("/upload")
async def upload_audio(request: Request, file: UploadFile = File(...)):
    host = request.headers.get("host", "")
    grade = get_grade_from_host(host)
    state = grade_states[grade]
    
    if not file.filename.lower().endswith(".mp3"):
        return {"success": False, "message": "只允许上传 .mp3 文件"}
    
    file_path = state["audio_dir"] / file.filename
    with open(file_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
        
    file_list = get_audio_files(grade)
    message = {
        "type": "audio_list",
        "files": file_list
    }
    for client in list(state["clients"]):
        try:
            await client.send_json(message)
        except:
            state["clients"].discard(client)
            
    return {"success": True, "filename": file.filename}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    host = websocket.headers.get("host", "")
    grade = get_grade_from_host(host)
    state = grade_states[grade]
    playback_state = state["playback_state"]
    clients = state["clients"]

    await websocket.accept()
    clients.add(websocket)
    print(f"客户端已连接 [{grade.upper()}]: {websocket.client}")
    await broadcast_client_count(grade)

    try:
        # 1. 下发当前年级的最新文件列表
        await websocket.send_json({
            "type": "audio_list",
            "files": get_audio_files(grade)
        })

        # 2. 如果当前有选中的文件（但处于 stopped/paused 状态），下发选中通知
        if playback_state["filename"] and playback_state["status"] != "playing":
            await websocket.send_json({
                "type": "audio:select",
                "filename": playback_state["filename"]
            })

        # 3. 💡 关键点 2：如果服务器正处于 playing 状态，立刻下发 schedule_play，由前端自动完成“下载+解码+追赶时间播放”
        if playback_state["status"] == "playing" and playback_state["filename"]:
            now = int(time.time() * 1000)
            current_pos = playback_state["start_pos"]
            if now >= playback_state["target_time"]:
                current_pos += (now - playback_state["target_time"]) / 1000.0
            
            sync_target_time = now + 4000
            target_pos_at_start = current_pos + 4.0

            await websocket.send_json({
                "type": "audio:schedule_play",
                "targetTime": sync_target_time,
                "audioUrl": f"/static/audio/{playback_state['filename']}",
                "filename": playback_state["filename"],
                "position": target_pos_at_start
            })
        elif playback_state["status"] == "paused":
            await websocket.send_json({
                "type": "audio:pause"
            })

        # 4. 消息监听循环
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "ntp:sync":
                t1 = int(time.time() * 1000)
                await websocket.send_json({
                    "type": "ntp:sync:reply",
                    "t0": data.get("t0"),
                    "t1": t1,
                    "t2": int(time.time() * 1000)
                })

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "refresh_files":
                await websocket.send_json({
                    "type": "audio_list",
                    "files": get_audio_files(grade)
                })

            elif msg_type == "cmd:select":
                filename = data.get("filename")
                playback_state["filename"] = filename
                playback_state["start_pos"] = 0.0

                message = {
                    "type": "audio:select",
                    "filename": filename
                }
                for client in list(clients):
                    try:
                        await client.send_json(message)
                    except:
                        clients.discard(client)

            elif msg_type == "cmd:play":
                filename = data.get("filename")
                start_position = data.get("position", 0.0)
                
                delay = 6000  
                target_time = int(time.time() * 1000) + delay

                playback_state["status"] = "playing"
                playback_state["filename"] = filename
                playback_state["target_time"] = target_time
                playback_state["start_pos"] = start_position

                message = {
                    "type": "audio:schedule_play",
                    "targetTime": target_time,
                    "audioUrl": f"/static/audio/{filename}",
                    "filename": filename,
                    "position": start_position
                }
                for client in list(clients):
                    try:
                        await client.send_json(message)
                    except:
                        clients.discard(client)

            elif msg_type == "cmd:pause":
                if playback_state["status"] == "playing":
                    now = int(time.time() * 1000)
                    if now >= playback_state["target_time"]:
                        playback_state["start_pos"] += (now - playback_state["target_time"]) / 1000.0
                playback_state["status"] = "paused"

                message = {"type": "audio:pause"}
                for client in list(clients):
                    try:
                        await client.send_json(message)
                    except:
                        clients.discard(client)

            elif msg_type == "cmd:stop":
                playback_state["status"] = "stopped"
                playback_state["filename"] = None
                playback_state["start_pos"] = 0.0

                message = {"type": "audio:stop"}
                for client in list(clients):
                    try:
                        await client.send_json(message)
                    except:
                        clients.discard(client)

    except WebSocketDisconnect:
        clients.remove(websocket)
        print(f"客户端已断开 [{grade.upper()}]: {websocket.client}")
        await broadcast_client_count(grade)

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, loop="uvloop", websocket_ping_interval=20, websocket_ping_timeout=30)
