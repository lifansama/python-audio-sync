import os
import time
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

AUDIO_DIR = Path("static/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

clients = set()

# 💡 维护一个全局的播放状态机
playback_state = {
    "status": "stopped",       # "playing", "paused", "stopped"
    "filename": None,
    "target_time": 0,          # 计划开播的绝对时间戳 (ms)
    "start_pos": 0.0           # 播放起始位置偏移量 (秒)
}

def get_audio_files():
    if not AUDIO_DIR.exists():
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    
    mp3_files = []
    for file in os.listdir(AUDIO_DIR):
        if file.lower().endswith(".mp3"):
            mp3_files.append(file)
    mp3_files.sort()
    return mp3_files

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

@app.post("/upload")
async def upload_audio(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".mp3"):
        return {"success": False, "message": "只允许上传 .mp3 文件"}
    
    file_path = AUDIO_DIR / file.filename
    with open(file_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
        
    file_list = get_audio_files()
    message = {
        "type": "audio_list",
        "files": file_list
    }
    for client in list(clients):
        try:
            await client.send_json(message)
        except:
            clients.discard(client)
            
    return {"success": True, "filename": file.filename}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    print(f"客户端已连接: {websocket.client}")

    try:
        # 1. 发送曲目列表
        await websocket.send_json({
            "type": "audio_list",
            "files": get_audio_files()
        })

        # 2. 💡 核心新功能：如果当前有正在播放或暂停的音频，立即向新连接的设备同步状态
        if playback_state["status"] == "playing":
            now = int(time.time() * 1000)
            current_pos = playback_state["start_pos"]
            # 计算截至目前的精确播放进度
            if now >= playback_state["target_time"]:
                current_pos += (now - playback_state["target_time"]) / 1000.0
            
            # 给新加入的设备留出 4 秒缓冲时间进行预加载和解码
            sync_target_time = now + 4000
            await websocket.send_json({
                "type": "audio:schedule_play",
                "targetTime": sync_target_time,
                "audioUrl": f"/static/audio/{playback_state['filename']}",
                "filename": playback_state["filename"],
                "position": current_pos
            })
        elif playback_state["status"] == "paused":
            await websocket.send_json({
                "type": "audio:select",
                "filename": playback_state["filename"]
            })
            await websocket.send_json({
                "type": "audio:pause"
            })

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
                    "files": get_audio_files()
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
                
                delay = 6000  # 公网环境 6 秒缓冲
                target_time = int(time.time() * 1000) + delay

                # 更新全局播放状态
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
                # 更新状态并结算当前已播放的进度
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
        print(f"客户端已断开: {websocket.client}")

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
