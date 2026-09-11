import os
import re
import subprocess
import asyncio
import soundfile as sf
from modules.text_normalizer import normalize_for_tts
from modules.tts_engine import (
    _generate_edge_tts_async,
    get_audio_duration,
    _create_atempo_filter,
    VOICE_NAME as _edge_voice,
)

SSML_TAG_RE = re.compile(r'<[^>]+>')

def _strip_ssml(text):
    return SSML_TAG_RE.sub('', text)

def _get_wav_duration(file_path):
    """Lấy thời lượng WAV trực tiếp qua header bằng soundfile (nhanh gấp 100x ffprobe)."""
    try:
        return sf.info(file_path).duration
    except Exception:
        return get_audio_duration(file_path)

_model = None

def _get_model():
    global _model
    if _model is None:
        from omnivoice import OmniVoice
        import torch
        print("Đang tải OmniVoice model (lần đầu sẽ download ~2.5GB)...")
        has_cuda = torch.cuda.is_available()
        if has_cuda:
            vram_mb = torch.cuda.get_device_properties(0).total_memory / 1024 / 1024
            print(f"Phát hiện GPU: {torch.cuda.get_device_name(0)} ({vram_mb:.0f}MB VRAM)")
            if vram_mb < 6000:
                print("VRAM < 6GB, chạy bằng CPU offload để tránh OOM...")
                _model = OmniVoice.from_pretrained(
                    "k2-fsa/OmniVoice",
                    device_map="cpu",
                )
            else:
                print("Nạp model lên GPU với định dạng FP16 (tận dụng Tensor Cores)...")
                _model = OmniVoice.from_pretrained(
                    "k2-fsa/OmniVoice",
                    device_map="cuda:0",
                    dtype=torch.float16,
                )
        else:
            print("Không có GPU, chạy bằng CPU (chậm hơn)...")
            _model = OmniVoice.from_pretrained(
                "k2-fsa/OmniVoice",
                device_map="cpu",
            )
        print("OmniVoice model đã sẵn sàng!")
    return _model

def _group_segments(segments, max_gap=0.45, max_group_dur=10.0, max_chars=180):
    """
    Gom các segment vụn (Whisper hay ngắt ngắn 1-2 từ) thành cụm câu hoàn chỉnh.
    Giúp giảm 60-70% số lượt gọi model và cho giọng đọc mượt mà, tự nhiên hơn.
    """
    valid = [s for s in segments if s.get('translated_text', '').strip()]
    if not valid:
        return []

    groups = []
    current_group = [valid[0]]

    for seg in valid[1:]:
        prev = current_group[-1]
        gap = seg['start'] - prev['end']
        group_dur = seg['end'] - current_group[0]['start']
        curr_chars = sum(len(s.get('translated_text', '')) for s in current_group)
        new_chars = curr_chars + len(seg.get('translated_text', ''))

        if 0 <= gap <= max_gap and group_dur <= max_group_dur and new_chars <= max_chars:
            current_group.append(seg)
        else:
            groups.append(current_group)
            current_group = [seg]

    if current_group:
        groups.append(current_group)

    return groups

def _prepare_ref_audio(ref_audio_path, temp_dir):
    """
    Chuẩn hóa audio mẫu cho Voice Clone:
    OmniVoice đạt chất lượng tốt nhất và nhanh nhất với mẫu từ 4 đến 7 giây.
    Nếu audio dài hơn 8 giây, tự động cắt lấy 7 giây đầu để giảm tính toán Cross-Attention O(N^2),
    tăng tốc độ sinh giọng từ 2x đến 4x và giảm ngốn VRAM.
    """
    if not ref_audio_path or not os.path.exists(ref_audio_path):
        return ref_audio_path
        
    try:
        dur = _get_wav_duration(ref_audio_path)
        if dur > 8.0:
            print(f"Audio mẫu dài {dur:.1f}s, tự động cắt 7.0s tối ưu nhất để tăng tốc Clone giọng...")
            trimmed_path = os.path.join(temp_dir, 'ref_audio_trimmed.wav')
            data, sr = sf.read(ref_audio_path)
            max_samples = int(sr * 7.0)
            sf.write(trimmed_path, data[:max_samples], sr)
            return trimmed_path
    except Exception as e:
        print(f"Không thể cắt audio mẫu: {e}")
    return ref_audio_path

def generate_omnivoice_tts(segments, temp_dir, voice_mode="auto", voice_instruct=None, ref_audio_path=None):
    print(f"Đang tổng hợp giọng nói từ OmniVoice (local GPU) - mode: {voice_mode}...")

    model = _get_model()

    tts_chunks_dir = os.path.join(temp_dir, 'tts_chunks')
    os.makedirs(tts_chunks_dir, exist_ok=True)

    # Pre-load ref_text cho clone mode và cache lại
    ref_text = None
    if ref_audio_path and voice_mode == "clone":
        ref_audio_path = _prepare_ref_audio(ref_audio_path, temp_dir)
        ref_text_file = os.path.join(os.path.dirname(ref_audio_path), 'ref_text.txt')
        if os.path.exists(ref_text_file):
            try:
                with open(ref_text_file, 'r', encoding='utf-8') as f:
                    ref_text = f.read().strip()
                print(f"Nạp transcript mẫu từ cache: {ref_text[:60]}...")
            except Exception:
                ref_text = None

        if not ref_text:
            from modules.stt_engine import transcribe_audio
            ref_segments = transcribe_audio(ref_audio_path, model_size="tiny")
            if ref_segments:
                ref_text = " ".join(seg['text'] for seg in ref_segments)
                print(f"Tự động nhận dạng transcript từ audio mẫu: {ref_text[:60]}...")
                try:
                    with open(ref_text_file, 'w', encoding='utf-8') as f:
                        f.write(ref_text)
                except Exception:
                    pass
            else:
                print("Cảnh báo: Không thể nhận diện transcript từ audio mẫu, clone có thể kém chính xác")

    # Gom các segment ngắn thành cụm câu tự nhiên
    groups = _group_segments(segments)
    print(f"Tối ưu gom câu: Gom {len(segments)} segments vụn thành {len(groups)} cụm câu tự nhiên (giảm ~{(1 - len(groups)/max(len(segments), 1))*100:.0f}% lượt sinh).")

    # 1. Sinh âm thanh cho từng cụm câu
    for i, group in enumerate(groups):
        combined_text = " ".join(s.get('translated_text', "").strip() for s in group)
        if not combined_text.strip():
            continue

        orig_text = normalize_for_tts(combined_text)
        clean_text = _strip_ssml(orig_text)

        out_path = os.path.join(tts_chunks_dir, f'chunk_{i}.wav')
        if os.path.exists(out_path):
            continue

        try:
            kwargs = {
                "text": clean_text,
                "num_step": 16,  # Tối ưu 16 bước khuếch tán (nhanh gấp đôi so với 32 mặc định)
            }
            if voice_instruct:
                kwargs["instruct"] = voice_instruct
            elif voice_mode == "clone" and ref_audio_path:
                kwargs["ref_audio"] = ref_audio_path
                if ref_text:
                    kwargs["ref_text"] = ref_text

            audio_list = model.generate(**kwargs)
            sf.write(out_path, audio_list[0], 24000)
            print(f"[{i+1}/{len(groups)}] OmniVoice OK: {clean_text[:40]}...")
        except Exception as e:
            print(f"Cảnh báo: cụm {i} OmniVoice lỗi ({e}), dùng edge-tts fallback...")
            fallback_mp3 = os.path.join(tts_chunks_dir, f'fallback_{i}.mp3')
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    _generate_edge_tts_async(orig_text, fallback_mp3)
                )
            finally:
                loop.close()
            fallback_wav = os.path.join(tts_chunks_dir, f'chunk_{i}_fallback.wav')
            subprocess.run(
                ['ffmpeg', '-i', fallback_mp3, '-ar', '44100', fallback_wav, '-y'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            os.replace(fallback_wav, out_path)

    # 2. Căn chỉnh timeline và ghép nối file
    concat_list_path = os.path.join(temp_dir, 'concat_list.txt')
    concat_lines = []
    current_time = 0.0

    for i, group in enumerate(groups):
        start_sec = group[0]['start']
        end_sec = group[-1]['end']
        target_duration = end_sec - start_sec

        orig_wav = os.path.join(tts_chunks_dir, f'chunk_{i}.wav')
        if not os.path.exists(orig_wav):
            continue

        gap = start_sec - current_time
        if gap > 0.001:
            silence_path = os.path.join(tts_chunks_dir, f'silence_{i}.wav')
            subprocess.run(
                ['ffmpeg', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', f'{gap:.4f}', silence_path, '-y'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            concat_lines.append(f"file '{silence_path}'")
            current_time += gap

        actual_duration = _get_wav_duration(orig_wav)
        if actual_duration <= 0:
            continue

        speed_factor = actual_duration / target_duration
        MAX_SPEED = 2.5
        MIN_SPEED = 0.8
        clamped_speed = max(MIN_SPEED, min(MAX_SPEED, speed_factor))

        processed_wav = os.path.join(tts_chunks_dir, f'processed_{i}.wav')
        atempo_filter = _create_atempo_filter(clamped_speed)
        subprocess.run(
            ['ffmpeg', '-i', orig_wav, '-filter:a', atempo_filter, '-ar', '44100', processed_wav, '-y'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        concat_lines.append(f"file '{processed_wav}'")
        final_dur = _get_wav_duration(processed_wav)
        current_time += final_dur
        print(f"[{i+1}/{len(groups)}] Đã căn chỉnh cụm: {int(start_sec*1000)}ms -> {int(end_sec*1000)}ms (thực tế: {final_dur:.2f}s)")

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

    # 3. Giải phóng VRAM GPU sau khi hoàn tất
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

    print(f"Đã tạo giọng đọc OmniVoice thành công! Lưu tại: {output_path}")
    return output_path