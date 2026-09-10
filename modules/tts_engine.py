import os
import html
import asyncio
import edge_tts
import subprocess
from modules.text_normalizer import normalize_for_tts, wrap_unknown_proper_nouns

VOICE_NAME = "vi-VN-HoaiMyNeural" 

def set_voice_name(name):
    global VOICE_NAME
    VOICE_NAME = name

async def _generate_edge_tts_async(text, output_path):
    import random
    
    # Bước 1: Chuẩn hóa text
    normalized_text = normalize_for_tts(text)
    normalized_text = wrap_unknown_proper_nouns(normalized_text)
    
    # Thử gọi API tối đa 3 lần với exponential backoff
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(normalized_text, VOICE_NAME)
            await communicate.save(output_path)
            return  # Thành công, thoát vòng lặp
        except Exception as e:
            if attempt == 2:
                raise e
            wait_time = (2 ** attempt) + random.uniform(0.5, 1.5)
            await asyncio.sleep(wait_time)

async def generate_all_tts(segments, temp_dir):
    """Hàm tạo tất cả âm thanh song song bằng async semaphore để tránh lỗi event loop per segment."""
    sem = asyncio.Semaphore(3)
    
    async def process_segment(i, text, path):
        async with sem:
            await _generate_edge_tts_async(text, path)
            
    tasks = []
    tts_chunks_dir = os.path.join(temp_dir, 'tts_chunks')
    os.makedirs(tts_chunks_dir, exist_ok=True)
    
    for i, segment in enumerate(segments):
        text = segment.get('translated_text', "")
        if text.strip():
            temp_tts_path = os.path.join(tts_chunks_dir, f'chunk_{i}.mp3')
            # Cấu hình cache cho TTS: nếu file đã tồn tại thì skip tải lại
            if not os.path.exists(temp_tts_path):
                tasks.append(process_segment(i, text, temp_tts_path))
            
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            print(f"Cảnh báo: Một phân đoạn TTS bị lỗi (có thể do text chứa ký tự đặc biệt) - {r}")

def get_audio_duration(file_path):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
             '-of', 'default=noprint_wrappers=1:nokey=1', file_path],
            capture_output=True, text=True
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0

def _create_atempo_filter(speed_factor):
    clamped = max(0.5, min(10.0, speed_factor))
    filters = []
    while clamped > 2.0:
        filters.append('atempo=2.0')
        clamped /= 2.0
    while clamped < 0.5:
        filters.append('atempo=0.5')
        clamped *= 2.0
    filters.append(f'atempo={clamped:.6f}')
    return ','.join(filters)

def generate_voiceover(segments, video_duration=None, engine="edge",
                       voice_mode="auto", voice_instruct=None, ref_audio_path=None):
    """
    Tạo giọng đọc từ văn bản dịch bằng TTS engine được chọn, xử lý tốc độ, thêm khoảng lặng và nối audio.
    Sử dụng file-based concat qua FFmpeg để tránh OOM trên RAM.
    Hỗ trợ engine: "edge" (mặc định), "omnivoice".
    voice_mode: "auto" | "design" | "clone" (chỉ dùng cho omnivoice)
    """
    if engine == "omnivoice":
        from modules.tts_engine_omnivoice import generate_omnivoice_tts
        base_dir = os.path.dirname(os.path.dirname(__file__))
        temp_dir = os.path.join(base_dir, 'temp')
        return generate_omnivoice_tts(segments, temp_dir, voice_mode, voice_instruct, ref_audio_path)

    print(f"Đang tạo giọng đọc chuẩn Việt ({VOICE_NAME}) và căn chỉnh timeline...")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    temp_dir = os.path.join(base_dir, 'temp')
    tts_chunks_dir = os.path.join(temp_dir, 'tts_chunks')
    os.makedirs(tts_chunks_dir, exist_ok=True)
    
    # 1. Khởi chạy toàn bộ TTS API call (I/O bound) đồng thời bằng asyncio
    print("Đang tổng hợp giọng nói từ Microsoft Edge TTS...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(generate_all_tts(segments, temp_dir))
    finally:
        loop.close()
    
    concat_list_path = os.path.join(temp_dir, 'concat_list.txt')
    concat_lines = []
    
    current_time = 0.0
    drift = 0.0
    
    # 2. Xử lý căn chỉnh audio timeline tuần tự bằng subprocess FFmpeg
    for i, segment in enumerate(segments):
        text = segment.get('translated_text', "")
        if not text.strip():
            continue
            
        start_sec = segment['start']
        end_sec = segment['end']
        target_duration = end_sec - start_sec
        
        orig_mp3 = os.path.join(tts_chunks_dir, f'chunk_{i}.mp3')
        if not os.path.exists(orig_mp3):
            continue
            
        # Thêm khoảng lặng nếu cần
        gap = start_sec - current_time
        if gap > 0.001:
            silence_path = os.path.join(tts_chunks_dir, f'silence_{i}.wav')
            subprocess.run(
                ['ffmpeg', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', f'{gap:.4f}', silence_path, '-y'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            concat_lines.append(f"file '{silence_path}'")
            current_time += gap
        else:
            drift += gap
            
        actual_duration = get_audio_duration(orig_mp3)
        if actual_duration <= 0:
            continue
            
        speed_factor = actual_duration / target_duration
        MAX_SPEED = 2.5
        MIN_SPEED = 0.8
        clamped_speed = max(MIN_SPEED, min(MAX_SPEED, speed_factor))
        if speed_factor != clamped_speed:
            print(f"Cảnh báo: segment {i} có speed_factor={speed_factor:.2f}, ép về {clamped_speed:.2f}")
        
        processed_wav = os.path.join(tts_chunks_dir, f'processed_{i}.wav')
        
        # Thay đổi tốc độ và chuyển đổi sang wav để chuẩn bị concat
        atempo_filter = _create_atempo_filter(clamped_speed)
        subprocess.run(
            ['ffmpeg', '-i', orig_mp3, '-filter:a', atempo_filter, '-ar', '44100', processed_wav, '-y'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        
        concat_lines.append(f"file '{processed_wav}'")
        final_dur = get_audio_duration(processed_wav)
        current_time += final_dur
        print(f"[{i+1}/{len(segments)}] Đã căn chỉnh xong đoạn: {int(start_sec*1000)}ms -> {int(end_sec*1000)}ms (thực tế: {final_dur:.2f}s)")
        
    with open(concat_list_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(concat_lines))
        
    merged_wav = os.path.join(temp_dir, 'merged_temp.wav')
    
    # 4. Concat file bằng FFmpeg re-encode
    subprocess.run(
        ['ffmpeg', '-f', 'concat', '-safe', '0', '-i', concat_list_path, '-c:a', 'pcm_s16le', '-ar', '44100', '-ac', '1', merged_wav, '-y'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    
    output_path = os.path.join(temp_dir, 'merged_audio.mp3')
    
    # 5. Encode lại thành mp3
    subprocess.run(
        ['ffmpeg', '-i', merged_wav, '-codec:a', 'libmp3lame', output_path, '-y'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    
    # Trim nếu dài hơn video
    if video_duration:
        dur = get_audio_duration(output_path)
        if dur > video_duration:
            trimmed = output_path.replace('.mp3', '_trimmed.mp3')
            subprocess.run(
                ['ffmpeg', '-i', output_path, '-t', str(video_duration), '-c', 'copy', trimmed, '-y'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            os.replace(trimmed, output_path)
            
    print(f"Đã tạo giọng đọc EdgeTTS thành công! Lưu tại: {output_path}")
    return output_path
