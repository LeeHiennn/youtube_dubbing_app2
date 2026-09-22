import os
import re
import subprocess
import asyncio
import soundfile as sf
from modules.text_normalizer import normalize_for_tts
from modules.sentence_grouper import group_into_sentences
from modules.tts_engine import (
    _generate_edge_tts_async,
    get_audio_duration,
    align_and_concat,
    VOICE_NAME as _edge_voice,
    NATURAL_RATE,
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


def _prepare_ref_audio(ref_audio_path, temp_dir):
    """
    Chuẩn hóa audio mẫu cho Voice Clone:
    - Chuyển đổi mọi định dạng (mp3, m4a, wav, ogg, webm...) sang WAV mono 24000Hz PCM 16-bit chuẩn bằng FFmpeg.
    - Cắt tối ưu 7 giây đầu để giảm tính toán và tránh tràn VRAM trên Colab GPU.
    """
    if not ref_audio_path or not os.path.exists(ref_audio_path):
        return ref_audio_path

    try:
        norm_wav = os.path.join(temp_dir, 'ref_audio_norm24k.wav')
        cmd = [
            'ffmpeg', '-y', '-i', ref_audio_path,
            '-t', '7.0',
            '-ar', '24000',
            '-ac', '1',
            '-c:a', 'pcm_s16le',
            norm_wav
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and os.path.exists(norm_wav) and os.path.getsize(norm_wav) > 1000:
            print("Đã chuẩn hóa audio mẫu sang mono 24kHz (7s) bằng FFmpeg thành công!")
            return norm_wav
    except Exception as e:
        print(f"Lỗi chuẩn hóa audio mẫu: {e}")

    return ref_audio_path


def _transcribe_ref_audio_safe(ref_audio_path):
    """
    Bóc băng audio mẫu an toàn, KHÔNG đụng chạm file transcript.json của video chính.
    """
    try:
        import whisper
        import torch
        # Nạp whisper tiny trên CPU để không tốn VRAM GPU của OmniVoice
        w_model = whisper.load_model("tiny", device="cpu")
        res = w_model.transcribe(ref_audio_path)
        text = res.get('text', '').strip()
        del w_model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return text if text else None
    except Exception as e:
        print(f"Không thể nhận diện transcript audio mẫu: {e}")
        return None


def generate_omnivoice_tts(segments, temp_dir, voice_mode="auto", voice_instruct=None, ref_audio_path=None):
    print(f"Đang tổng hợp giọng nói từ OmniVoice (local GPU) - mode: {voice_mode}...")

    model = _get_model()

    tts_chunks_dir = os.path.join(temp_dir, 'tts_chunks')
    os.makedirs(tts_chunks_dir, exist_ok=True)

    # 1. Chuẩn bị VoiceClonePrompt tái sử dụng (pre-computed prompt)
    clone_prompt = None
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
            ref_text = _transcribe_ref_audio_safe(ref_audio_path)
            if ref_text:
                print(f"Tự động nhận dạng transcript từ audio mẫu: {ref_text[:60]}...")
                try:
                    with open(ref_text_file, 'w', encoding='utf-8') as f:
                        f.write(ref_text)
                except Exception:
                    pass

        try:
            print("Đang tạo VoiceClonePrompt từ audio mẫu (tính 1 lần duy nhất cho toàn bộ video)...")
            clone_prompt = model.create_voice_clone_prompt(ref_audio_path, ref_text=ref_text)
            print("✅ VoiceClonePrompt đã khởi tạo thành công!")
        except Exception as e:
            print(f"Cảnh báo: create_voice_clone_prompt lỗi ({e}), chuyển sang truyền trực tiếp ref_audio...")
            clone_prompt = None

    # Gom các segment thành cụm câu tự nhiên (dùng shared grouper)
    groups = group_into_sentences(segments, text_key='translated_text')
    print(f"Tối ưu gom câu: Gom {len(segments)} segments vụn thành {len(groups)} cụm câu tự nhiên "
          f"(giảm ~{(1 - len(groups)/max(len(segments), 1))*100:.0f}% lượt sinh).")

    # 2. Sinh âm thanh cho từng cụm câu
    auto_clone_prompt = None

    for i, group in enumerate(groups):
        combined_text = " ".join(s.get('translated_text', "").strip() for s in group)
        if not combined_text.strip():
            continue

        orig_text = normalize_for_tts(combined_text)
        clean_text = _strip_ssml(orig_text)

        out_path = os.path.join(tts_chunks_dir, f'chunk_{i}.wav')
        if os.path.exists(out_path):
            # Nếu chunk đã tồn tại và chưa có auto prompt → thử tạo từ chunk_0
            if voice_mode == "auto" and auto_clone_prompt is None:
                try:
                    auto_clone_prompt = model.create_voice_clone_prompt(out_path, ref_text=clean_text)
                except Exception:
                    pass
            continue

        try:
            kwargs = {
                "text": clean_text,
                "num_step": 16,
            }
            if voice_instruct:
                kwargs["instruct"] = voice_instruct
            elif voice_mode == "clone":
                if clone_prompt is not None:
                    kwargs["voice_clone_prompt"] = clone_prompt
                elif ref_audio_path:
                    kwargs["ref_audio"] = ref_audio_path
                    if ref_text:
                        kwargs["ref_text"] = ref_text
            elif voice_mode == "auto" and auto_clone_prompt is not None:
                kwargs["voice_clone_prompt"] = auto_clone_prompt

            audio_list = model.generate(**kwargs)
            sf.write(out_path, audio_list[0], 24000)

            # Lưu chunk đầu tiên làm giọng mẫu cho auto mode
            if voice_mode == "auto" and auto_clone_prompt is None:
                try:
                    auto_clone_prompt = model.create_voice_clone_prompt(out_path, ref_text=clean_text)
                except Exception:
                    pass
                print(f"[{i+1}/{len(groups)}] OmniVoice OK (giọng mẫu auto): {clean_text[:40]}...")
            else:
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

    # 2. Căn chỉnh timeline và ghép nối bằng hàm chung
    output_path = align_and_concat(
        groups, tts_chunks_dir, temp_dir,
        get_duration_fn=_get_wav_duration, chunk_ext='.wav'
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