from modules.translator import translate_segments
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

test = [
    {"start":0,"end":2,"text":"你好，这是一个测试"}, 
    {"start":2,"end":4,"text":"Hello world"}, 
    {"start":4,"end":6,"text":"Xin chào các bạn"}
]

out = translate_segments(test)

print("\n--- KET QUA ---")
for s in out:
    print(f"Ban goc: {s['text']} | Dich: {s.get('translated_text', '')} | Nguon: {s.get('source_lang', '')}")
