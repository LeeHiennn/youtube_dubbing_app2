import os
import html
import asyncio
import edge_tts
import subprocess
from modules.text_normalizer import normalize_for_tts, wrap_unknown_proper_nouns
from modules.sentence_grouper import group_into_sentences

VOICE_NAME = "vi-VN-HoaiMyNeural"
NATURAL_RATE = "+5%"  # Tốc độ đọc hơi nhanh hơn mặc định để khớp timeline

# Giới hạn tốc độ tự nhiên — KHÔNG ép biến dạng giọng
MIN_SPEED_NATURAL = 0.9
MAX_SPEED_NATURAL = 1.3

# Khoảng dừng tối thiểu giữa các cụm câu (giây)
MIN_GAP = 0.12

# Fade in/out (giây) mỗi cụm
FADE_DURATION = 0.015


def set_voice_name(name):
    global VOICE_NAME
    VOICE_NAME = name


async def _generate_edge_tts_async(text, output_path, rate=None):
    import random

    normalized_text = normalize_for_tts(text)
    normalized_text = wrap_unknown_proper_nouns(normalized_text)

    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(normalized_text, VOICE_NAME, rate=rate or NATURAL_RATE)
            await communicate.save(output_path)
            return
        except Exception as e:
            if attempt == 2:
                raise e
            wait_time = (2 ** attempt) + random.uniform(0.5, 1.5)
            await asyncio.sleep(wait_time)


async def _generate_all_tts_groups(groups, tts_chunks_dir, rate=None):
    """Sinh TTS cho tất cả cụm câu song song."""
    sem = asyncio.Semaphore(8)

    async def process_group(i, text, path):
        async with sem:
            await _generate_edge_tts_async(text, path, rate=rate)

    tasks = []
    for i, group in enumerate(groups):
        combined_text = " ".join(s.get('translated_text', "").strip() for s in group)
        if not combined_text.strip():
            continue

        temp_tts_path = os.path.join(tts_chunks_dir, f'chunk_{i}.mp3')
        if not os.path.exists(temp_tts_path):
            tasks.append(process_group(i, combined_text, temp_tts_path))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            print(f"Cảnh báo: Một cụm TTS bị lỗi - {r}")


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


def align_and_concat(groups, tts_chunks_dir, temp_dir, video_duration=None,
                     get_duration_fn=None, chunk_ext='.mp3'):
    """
    Hàm CHUNG căn chỉnh timeline + concat audio cho cả Edge TTS và OmniVoice.
    
    - Chấp nhận drift nhẹ thay vì ép tốc độ biến dạng giọng
    - Fade in/out 15ms mỗi cụm
    - Khoảng dừng tối thiểu MIN_GAP giữa các cụm
    """
    if get_duration_fn is None:
        get_duration_fn = get_audio_duration

    concat_list_path = os.path.join(temp_dir, 'concat_list.txt')
    concat_lines = []
    current_time = 0.0

    for i, group in enumerate(groups):
        start_sec = group[0]['start']
        end_sec = group[-1]['end']
        target_duration = end_sec - start_sec

        orig_file = os.path.join(tts_chunks_dir, f'chunk_{i}{chunk_ext}')
        if not os.path.exists(orig_file):
            print(f"⚠️ Cụm {i}: File chunk không tồn tại, bỏ qua")
            continue

        # Chèn khoảng lặng
        gap = start_sec - current_time
        if gap < MIN_GAP and gap > 0.001:
            gap = MIN_GAP  # Đảm bảo khoảng dừng tối thiểu
        if gap > 0.001:
            silence_path = os.path.join(tts_chunks_dir, f'silence_{i}.wav')
            subprocess.run(
                ['ffmpeg', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
                 '-t', f'{gap:.4f}', silence_path, '-y'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            concat_lines.append(f"file '{silence_path}'")
            current_time += gap

        actual_duration = get_duration_fn(orig_file)
        if actual_duration <= 0:
            print(f"⚠️ Cụm {i}: Không đọc được duration, dùng file gốc trực tiếp...")
            # Thử convert sang WAV chuẩn và dùng luôn
            rescue_wav = os.path.join(tts_chunks_dir, f'rescue_{i}.wav')
            subprocess.run(
                ['ffmpeg', '-i', orig_file, '-ar', '44100', '-ac', '1', rescue_wav, '-y'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if os.path.exists(rescue_wav) and os.path.getsize(rescue_wav) > 1000:
                concat_lines.append(f"file '{rescue_wav}'")
                rescue_dur = get_duration_fn(rescue_wav)
                current_time += rescue_dur if rescue_dur > 0 else target_duration
                print(f"[{i+1}/{len(groups)}] Đã cứu cụm {i} bằng re-encode")
            else:
                print(f"❌ Cụm {i}: Không thể cứu, bỏ qua")
            continue

        speed_factor = actual_duration / target_duration if target_duration > 0 else 1.0
        processed_wav = os.path.join(tts_chunks_dir, f'processed_{i}.wav')

        # Quyết định tốc độ: chỉ điều chỉnh nhẹ trong khoảng tự nhiên
        if MIN_SPEED_NATURAL <= speed_factor <= MAX_SPEED_NATURAL:
            # Trong khoảng tự nhiên → áp dụng atempo
            atempo_filter = _create_atempo_filter(speed_factor)
            result = subprocess.run(
                ['ffmpeg', '-i', orig_file, '-filter:a', atempo_filter,
                 '-ar', '44100', processed_wav, '-y'],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )
        else:
            # Ngoài khoảng tự nhiên → CHẤP NHẬN DRIFT, giữ tốc độ gốc
            if speed_factor > MAX_SPEED_NATURAL:
                print(f"⚠️ Cụm {i}: TTS dài hơn gốc {speed_factor:.2f}x → chấp nhận drift (không ép tốc)")
            elif speed_factor < MIN_SPEED_NATURAL:
                print(f"⚠️ Cụm {i}: TTS ngắn hơn gốc {speed_factor:.2f}x → chấp nhận drift")
            # Copy sang WAV chuẩn (không đổi tốc)
            result = subprocess.run(
                ['ffmpeg', '-i', orig_file, '-ar', '44100', '-ac', '1', processed_wav, '-y'],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )

        # Kiểm tra file output có hợp lệ không
        if not os.path.exists(processed_wav) or os.path.getsize(processed_wav) < 1000:
            print(f"⚠️ Cụm {i}: FFmpeg xử lý thất bại, dùng file gốc...")
            # Fallback: dùng file gốc trực tiếp
            subprocess.run(
                ['ffmpeg', '-i', orig_file, '-ar', '44100', '-ac', '1', processed_wav, '-y'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

        # Kiểm tra lần cuối
        if os.path.exists(processed_wav) and os.path.getsize(processed_wav) > 1000:
            concat_lines.append(f"file '{processed_wav}'")
            final_dur = get_duration_fn(processed_wav)
            if final_dur <= 0:
                final_dur = actual_duration  # Fallback duration
            current_time += final_dur
            print(f"[{i+1}/{len(groups)}] Đã căn chỉnh cụm: {int(start_sec*1000)}ms → {int(end_sec*1000)}ms "
                  f"(thực tế: {final_dur:.2f}s, target: {target_duration:.2f}s)")
        else:
            print(f"❌ Cụm {i}: Mất hoàn toàn, không thể phục hồi")

    with open(concat_list_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(concat_lines))

    merged_wav = os.path.join(temp_dir, 'merged_temp.wav')
    subprocess.run(
        ['ffmpeg', '-f', 'concat', '-safe', '0', '-i', concat_list_path,
         '-c:a', 'pcm_s16le', '-ar', '44100', '-ac', '1', merged_wav, '-y'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    output_path = os.path.join(temp_dir, 'merged_audio.mp3')
    subprocess.run(
        ['ffmpeg', '-i', merged_wav, '-codec:a', 'libmp3lame', output_path, '-y'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # Trim nếu dài hơn video
    if video_duration:
        dur = get_duration_fn(output_path)
        if dur > video_duration:
            trimmed = output_path.replace('.mp3', '_trimmed.mp3')
            subprocess.run(
                ['ffmpeg', '-i', output_path, '-t', str(video_duration),
                 '-c', 'copy', trimmed, '-y'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            os.replace(trimmed, output_path)

    return output_path


def generate_voiceover(segments, video_duration=None, engine="edge",
                       voice_mode="auto", voice_instruct=None, ref_audio_path=None):
    """
    Tạo giọng đọc từ văn bản dịch bằng TTS engine được chọn.
    Gom segment thành cụm câu để giọng đọc liền mạch, tự nhiên.
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

    # Gom segment thành cụm câu
    groups = group_into_sentences(segments, text_key='translated_text')
    print(f"Tối ưu gom câu: {len(segments)} segments → {len(groups)} cụm câu "
          f"(giảm ~{(1 - len(groups)/max(len(segments), 1))*100:.0f}% lượt TTS)")

    # Sinh TTS cho từng cụm câu (song song)
    print("Đang tổng hợp giọng nói từ Microsoft Edge TTS...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_generate_all_tts_groups(groups, tts_chunks_dir, rate=NATURAL_RATE))
    finally:
        loop.close()

    # Căn chỉnh timeline và ghép nối
    output_path = align_and_concat(groups, tts_chunks_dir, temp_dir,
                                   video_duration=video_duration, chunk_ext='.mp3')

    print(f"Đã tạo giọng đọc EdgeTTS thành công! Lưu tại: {output_path}")
    return output_path
