import os
import time
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

app = FastAPI()

# 托管 static 文件夹 (包含 html 和 audio 资源)
app.mount("/static", StaticFiles(directory="static"), name="static")

AUDIO_DIR = Path("static/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# 保持所有已连接的 WebSocket
clients = set()

def get_audio_files():
    """扫描 static/audio/ 目录下所有的 .mp3 文件"""
    if not AUDIO_DIR.exists():
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    
    mp3_files = []
    for file in os.listdir(AUDIO_DIR):
        if file.lower().endswith(".mp3"):
            mp3_files.append(file)
    mp3_files.sort()
    return mp3_files

# ====== 新增：根路径直接返回 index.html ======
@app.get("/")
async def read_index():
    return FileResponse("static/index.html")
# ============================================

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
            pass
            
    return {"success": True, "filename": file.filename}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    print(f"客户端已连接: {websocket.client}")

    await websocket.send_json({
        "type": "audio_list",
        "files": get_audio_files()
    })

    try:
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

            elif msg_type == "refresh_files":
                await websocket.send_json({
                    "type": "audio_list",
                    "files": get_audio_files()
                })

            elif msg_type == "cmd:select":
                filename = data.get("filename")
                message = {
                    "type": "audio:select",
                    "filename": filename
                }
                for client in list(clients):
                    await client.send_json(message)

            elif msg_type == "cmd:play":
                filename = data.get("filename")
                start_position = data.get("position", 0.0)
                
                delay = 5000  
                target_time = int(time.time() * 1000) + delay

                message = {
                    "type": "audio:schedule_play",
                    "targetTime": target_time,
                    "audioUrl": f"/static/audio/{filename}",
                    "filename": filename,
                    "position": start_position
                }
                for client in list(clients):
                    await client.send_json(message)

            elif msg_type == "cmd:pause":
                message = {"type": "audio:pause"}
                for client in list(clients):
                    await client.send_json(message)

            elif msg_type == "cmd:stop":
                message = {"type": "audio:stop"}
                for client in list(clients):
                    await client.send_json(message)

    except WebSocketDisconnect:
        clients.remove(websocket)
        print(f"客户端已断开: {websocket.client}")

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)