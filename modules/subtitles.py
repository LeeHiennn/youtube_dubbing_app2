import os
import subprocess
import re
import math

def format_srt_ts(sec: float) -> str:
    """Định dạng thời gian cho SRT: HH:MM:SS,mmm"""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        s += ms // 1000
        ms = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _format_ass_ts(sec: float) -> str:
    """Định dạng thời gian cho ASS: H:MM:SS.cs"""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        s += cs // 100
        cs = cs % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def _wrap_text(text, max_len=42):
    """Cắt text thành nhiều dòng tại vị trí khoảng trắng hoặc dấu phẩy gần nhất, max_len ký tự mỗi dòng."""
    if len(text) <= max_len:
        return text
    
    words = re.split(r'([ ,，])', text)
    lines = []
    current_line = ""
    for token in words:
        if len(current_line) + len(token) <= max_len:
            current_line += token
        else:
            if current_line:
                lines.append(current_line.strip())
            current_line = token.lstrip()
    if current_line:
        lines.append(current_line.strip())
    
    return "\n".join(lines)

def build_srt(segments, field='translated_text', out_path=None):
    """Xây dựng file SRT."""
    lines = []
    index = 1
    for seg in segments:
        text = seg.get(field, "").strip()
        if not text:
            continue
            
        start_ts = format_srt_ts(seg['start'])
        end_ts = format_srt_ts(seg['end'])
        
        wrapped_text = _wrap_text(text, 42)
        
        lines.append(str(index))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(wrapped_text)
        lines.append("")
        index += 1
        
    content = "\n".join(lines)
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(content)
    return out_path

def build_ass(segments, style='both', out_path=None):
    """Xây dựng file ASS."""
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: SubVi,Noto Sans CJK SC,36,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,1,2,10,10,40,1
Style: SubZh,Noto Sans CJK SC,30,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,8,10,10,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    def escape_ass(text):
        return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        
    events = []
    for seg in segments:
        start_ts = _format_ass_ts(seg['start'])
        end_ts = _format_ass_ts(seg['end'])
        
        orig_text = escape_ass(seg.get('text', '').strip())
        trans_text = escape_ass(seg.get('translated_text', '').strip())
        
        if style in ['both', 'zh'] and orig_text:
            events.append(f"Dialogue: 0,{start_ts},{end_ts},SubZh,,0,0,0,,{orig_text}")
        if style in ['both', 'vi'] and trans_text:
            events.append(f"Dialogue: 0,{start_ts},{end_ts},SubVi,,0,0,0,,{trans_text}")
            
    content = header + "\n".join(events) + "\n"
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(content)
    return out_path

def _has_nvenc():
    """Kiểm tra xem FFmpeg có hỗ trợ NVIDIA h264_nvenc không."""
    try:
        res = subprocess.run(
            ['ffmpeg', '-hide_banner', '-encoders'],
            capture_output=True, text=True, encoding='utf-8', errors='ignore'
        )
        return 'h264_nvenc' in res.stdout
    except Exception:
        return False

def burn_subtitles(video_in, sub_path, video_out, fonts_dir=None):
    """Hard-burn phụ đề vào video sử dụng ffmpeg.
    Tự động dùng GPU NVIDIA (h264_nvenc) nếu có, fallback CPU ultrafast."""
    # Escape đường dẫn cho ffmpeg filter trên Windows
    escaped_path = sub_path.replace('\\', '\\\\').replace(':', '\\:')
    
    # Xác định filter
    filter_name = 'subtitles' if sub_path.lower().endswith('.srt') else 'ass'
    filter_arg = f"{filter_name}='{escaped_path}'"
    
    if fonts_dir:
        esc_fonts_dir = fonts_dir.replace('\\', '\\\\').replace(':', '\\:')
        filter_arg += f":fontsdir='{esc_fonts_dir}'"
    
    # Chọn encoder: GPU (nhanh 5-10x) hoặc CPU ultrafast
    use_nvenc = _has_nvenc()
    if use_nvenc:
        print("⚡ Phát hiện NVIDIA GPU → dùng h264_nvenc để burn subtitle (nhanh gấp 5-10x)...")
        encode_args = ['-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '23']
    else:
        print("⚠️ Không có NVIDIA GPU → dùng CPU libx264 ultrafast...")
        encode_args = ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23']
    
    cmd = [
        'ffmpeg', '-y', '-i', video_in,
        '-vf', filter_arg,
        *encode_args,
        '-c:a', 'copy',
        video_out
    ]
    
    print(f"Chạy lệnh ffmpeg: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
    
    if result.returncode != 0:
        # Nếu nvenc lỗi (driver cũ, VRAM không đủ) → fallback CPU
        if use_nvenc:
            print("⚠️ h264_nvenc lỗi, fallback về CPU ultrafast...")
            cmd_fallback = [
                'ffmpeg', '-y', '-i', video_in,
                '-vf', filter_arg,
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
                '-c:a', 'copy',
                video_out
            ]
            result = subprocess.run(cmd_fallback, capture_output=True, text=True, encoding='utf-8', errors='ignore')
            if result.returncode != 0:
                raise Exception(f"Lỗi ffmpeg: {result.stderr}")
        else:
            raise Exception(f"Lỗi ffmpeg: {result.stderr}")
        
    return video_out

def check_support():
    """Kiểm tra xem hệ thống có hỗ trợ ffmpeg libass và các font CJK không."""
    info = {
        'has_libass': False,
        'cjk_fonts': []
    }
    
    try:
        res = subprocess.run(['ffmpeg', '-filters'], capture_output=True, text=True, encoding='utf-8', errors='ignore')
        if re.search(r'(subtitles|ass)', res.stdout, re.IGNORECASE):
            info['has_libass'] = True
    except Exception as e:
        print(f"Lỗi khi kiểm tra ffmpeg filters: {e}")
        
    try:
        res = subprocess.run(['fc-list'], capture_output=True, text=True, encoding='utf-8', errors='ignore')
        fonts = []
        for line in res.stdout.split('\n'):
            if re.search(r'(cjk|wqy|noto.*sc|source han)', line, re.IGNORECASE):
                fonts.append(line.strip())
        info['cjk_fonts'] = fonts
    except Exception as e:
        print("Lệnh fc-list không khả dụng trên hệ thống này. Trả về list font rỗng.")
        print("Gợi ý: Trên Windows, bạn có thể sử dụng các font hệ thống như SimSun, Microsoft YaHei.")
        info['cjk_fonts'] = []
        
    return info
