from gtts import gTTS
from modules.stt_engine import transcribe_audio
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("Tao file am thanh test tieng Trung...")
tts = gTTS("你好，这是一个测试", lang='zh-CN')
tts.save("test_zh.mp3")

print("\n--- TEST 1: transcribe_audio(path, language='zh') ---")
segs_zh = transcribe_audio("test_zh.mp3", model_size="tiny", language='zh')
for s in segs_zh:
    print(f"[{s['start']:.1f} - {s['end']:.1f}] {s.get('text', '')}")

print("\n--- TEST 2: transcribe_audio(path) ---")
segs_auto = transcribe_audio("test_zh.mp3", model_size="tiny")
for s in segs_auto:
    print(f"[{s['start']:.1f} - {s['end']:.1f}] {s.get('text', '')}")

if os.path.exists("test_zh.mp3"):
    os.remove("test_zh.mp3")
if os.path.exists("transcript.json"):
    os.remove("transcript.json")
if os.path.exists("stt_checkpoint.json"):
    os.remove("stt_checkpoint.json")

print("\nKIEM CHUNG HOAN TAT!")
