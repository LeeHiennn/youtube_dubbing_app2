import os
import shutil
import subprocess
from main import process_video
from gtts import gTTS
import time

def main():
    print("=== TEST 1: Tuyến EN (Me at the zoo) ===")
    if os.path.exists('temp'):
        shutil.rmtree('temp')
        
    url_en = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    res_en = process_video(url_en, "Nữ (Hoài My)", source_lang="en", sub_style="vi", burn_subs=True)
    
    print("\nKết quả EN:")
    for k, v in res_en.items():
        if k != "segments": print(f"{k}: {v}")
    
    if res_en.get("subtitled_video"):
        subprocess.run(['ffmpeg', '-y', '-i', res_en["subtitled_video"], '-ss', '00:00:05', '-vframes', '1', 'frame_en.jpg'], capture_output=True)
        print("Đã chụp frame_en.jpg")
        
    print("\n=== TEST 2: Tuyến tiếng Trung & Cache ===")
    if os.path.exists('temp'):
        shutil.rmtree('temp')
    os.makedirs('temp', exist_ok=True)
    
    url_zh = url_en  # Kiểm thử cache bằng cách dùng chung URL EN nhưng đổi source_lang="zh"
    with open('temp/current_url.txt', 'w', encoding='utf-8') as f:
        f.write(url_zh)
        
    # Tạo audio giả lập tiếng Trung 
    print("Tạo video giả lập tiếng Trung (5s)...")
    tts = gTTS("大家好，欢迎来到我的频道。今天我们来测试一下中文字幕功能。", lang='zh-CN')
    tts.save('temp/video.mp3')
    
    # Tạo video dummy
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'testsrc=duration=5:size=1280x720:rate=30', '-i', 'temp/video.mp3', '-c:v', 'libx264', '-c:a', 'aac', 'temp/video.mp4'], capture_output=True)
    
    # Chạy pipeline ZH
    res_zh = process_video(url_zh, "Nữ (Hoài My)", source_lang="zh", sub_style="both", burn_subs=True)
    
    print("\nKết quả ZH:")
    for k, v in res_zh.items():
        if k != "segments": print(f"{k}: {v}")
        
    print("\nTranscript ZH:")
    for seg in res_zh.get("segments", []):
        print(f"[{seg['start']:.1f}-{seg['end']:.1f}] Gốc: {seg['text']} => Dịch: {seg['translated_text']}")
        
    if res_zh.get("subtitled_video"):
        subprocess.run(['ffmpeg', '-y', '-i', res_zh["subtitled_video"], '-ss', '00:00:02', '-vframes', '1', 'frame_zh.jpg'], capture_output=True)
        print("Đã chụp frame_zh.jpg")
        
if __name__ == "__main__":
    main()
