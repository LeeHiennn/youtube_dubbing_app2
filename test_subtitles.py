import os
import subprocess
from modules.subtitles import build_srt, build_ass, burn_subtitles, check_support
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def main():
    segments = [{
        "start": 0.5, 
        "end": 2.5, 
        "text": "你好，这是一个测试视频，测试超过四十二个字符的自动换行功能是否正常工作，请大家确认。", 
        "translated_text": "Xin chào, đây là một video thử nghiệm, kiểm tra xem tính năng tự động xuống dòng khi vượt quá bốn mươi hai ký tự có hoạt động chính xác không, mọi người xác nhận nhé."
    }]
    
    print("--- 1. Kiểm tra build_srt & build_ass ---")
    srt_path = build_srt(segments, field='translated_text', out_path='test_sub.srt')
    ass_both = build_ass(segments, style='both', out_path='test_both.ass')
    ass_zh = build_ass(segments, style='zh', out_path='test_zh.ass')
    ass_vi = build_ass(segments, style='vi', out_path='test_vi.ass')
    
    with open(srt_path, 'r', encoding='utf-8') as f:
        print("SRT Content:\n" + f.read() + "\n")
        
    print("--- 2. Kiểm tra burn_subtitles ---")
    try:
        burn_subtitles('test_vid.mp4', 'test_both.ass', 'test_out_both.mp4')
        burn_subtitles('test_vid.mp4', 'test_zh.ass', 'test_out_zh.mp4')
        burn_subtitles('test_vid.mp4', 'test_vi.ass', 'test_out_vi.mp4')
        burn_subtitles('test_vid.mp4', 'test_sub.srt', 'test_out_srt.mp4')
        print("Đốt phụ đề (burn) thành công cho cả 4 trường hợp.")
    except Exception as e:
        print(f"Lỗi khi burn: {e}")
        
    print("--- 3. FFprobe so sánh duration ---")
    def get_duration(fpath):
        res = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', fpath],
            capture_output=True, text=True, encoding='utf-8', errors='ignore'
        )
        return float(res.stdout.strip())
        
    try:
        dur_orig = get_duration('test_vid.mp4')
        dur_both = get_duration('test_out_both.mp4')
        print(f"Duration gốc: {dur_orig}s | Duration sau khi burn 'both': {dur_both}s")
    except Exception as e:
        print(f"Lỗi ffprobe: {e}")

    print("--- 4. Trích xuất screenshot ---")
    try:
        subprocess.run(['ffmpeg', '-y', '-i', 'test_out_both.mp4', '-ss', '00:00:01', '-vframes', '1', 'screenshot.jpg'], capture_output=True)
        if os.path.exists('screenshot.jpg'):
            print("Đã trích xuất screenshot thành công (chứa phụ đề burn).")
    except Exception as e:
        print(f"Lỗi chụp ảnh: {e}")
        
    print("--- 5. Kiểm tra check_support() ---")
    support = check_support()
    print(support)
    
if __name__ == '__main__':
    main()
